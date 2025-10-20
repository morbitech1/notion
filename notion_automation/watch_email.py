"""Email inbox watcher (pure async).


Public entrypoints:
    * :func:`run_watcher_async` – connect + supervise an async fetch loop.
    * :func:`handler_async` – default message handler creating Notion pages.

Tests that previously used the synchronous ``run_watcher`` now await
``run_watcher_async(..., once=True)``. A thin synchronous compatibility
wrapper can be implemented externally if ever needed (not provided here to
avoid API duplication).
"""

from __future__ import annotations

import argparse
import asyncio
import email
import logging
import os
import re
from email.message import Message
from typing import Optional, Set

from . import email_utils as eu
from . import imap_async as ia
from . import notion_utils as nu

logger = logging.getLogger(__name__)

AUTO_ARCHIVE_PROCESSED = os.getenv("AUTO_ARCHIVE_PROCESSED", "0") != "0"
IMAP_ARCHIVE_FOLDER = os.getenv("IMAP_ARCHIVE_FOLDER", "[Gmail]/All Mail")


async def handler_async(email_msg: Message, uid: int) -> bool:
    """Async primary email handler for new messages."""
    support_case_id = await nu.find_or_create_support_case(email_msg)
    if support_case_id is None:
        logger.debug("No support case created or found; skipping email record subject=%r",
                     eu.get_decoded_subject(email_msg))
        return False
    email_id = await nu.create_email_record(email_msg, support_case_id, uid)
    return email_id is not None


async def fetch_new_uids_async(imap: ia.AsyncImapClient, since_uid: Optional[int]) -> list[int]:
    """Enumerate new UIDs using a single UID FETCH range.

    This library standardizes on UID FETCH enumeration to avoid server
    variability around UID SEARCH support. We request `(UID)` for the
    tail range starting at last_seen+1 (or 1 on first run) and parse
    returned lines of the form: `* <seq> FETCH (UID <n>)`.
    """
    start = (since_uid + 1) if since_uid is not None else 1
    fetch_range = f"{start}:*"
    resp = await imap.uid('fetch', fetch_range, '(UID)')
    # Raise on non-OK so supervisor reconnects (instead of silently
    # continuing on a dead connection after a BYE / protocol error).
    if getattr(resp, 'result', 'NO') != 'OK':
        raise ConnectionError(
            f"UID FETCH failed for range {fetch_range}: {getattr(resp, 'result', None)}"
        )
    lines = getattr(resp, 'lines', []) or []
    uids: list[int] = []
    for line in lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        text = line.decode(errors='ignore')
        m = re.search(r'UID (\d+)', text)
        if m:
            try:
                val = int(m.group(1))
            except ValueError:
                continue
            if since_uid is None or val > since_uid:
                uids.append(val)
    return sorted(set(uids))


async def fetch_message_with_attrs_async(imap: ia.AsyncImapClient, uid: int) -> Message:
    try:
        resp = await imap.uid('fetch', str(uid), '(X-GM-THRID X-GM-MSGID RFC822)')
        if getattr(resp, 'result', 'NO') != 'OK':
            raise RuntimeError('extended fetch failed')
        lines = getattr(resp, 'lines', [])
        raw_bytes = b''
        thrid = None
        msgid = None
        for line in lines:
            if isinstance(line, (bytes, bytearray)):
                if b'RFC822' in line and b'X-GM-' in line:
                    text = line.decode(errors='ignore')
                    m_t = re.search(r'X-GM-THRID (\d+)', text)
                    if m_t:
                        thrid = m_t.group(1)
                    m_m = re.search(r'X-GM-MSGID (\d+)', text)
                    if m_m:
                        msgid = m_m.group(1)
                else:
                    raw_bytes += line
        if not raw_bytes:
            raise RuntimeError('no bytes in fetch')
        msg = email.message_from_bytes(raw_bytes)
        if thrid and not msg.get('X-GM-THRID'):
            msg['X-GM-THRID'] = thrid
        if msgid and not msg.get('X-GM-MSGID'):
            msg['X-GM-MSGID'] = msgid
        return msg
    except Exception:
        raw = await imap.uid('fetch', str(uid), '(RFC822)')
        if getattr(raw, 'result', 'NO') != 'OK':
            raise RuntimeError(f'Failed to fetch UID {uid}')
        lines = getattr(raw, 'lines', [])
        # Filter only byte/bytearray line entries when reconstructing message bytes
        content = b''.join([line for line in lines if isinstance(line, (bytes, bytearray))])
        return email.message_from_bytes(content)


async def handle_uid_async(imap: ia.AsyncImapClient, uid: int) -> None:
    msg = await fetch_message_with_attrs_async(imap, uid)
    handled = await handler_async(msg, uid)
    if not (AUTO_ARCHIVE_PROCESSED and handled):
        return
    try:
        await imap.uid('copy', str(uid), IMAP_ARCHIVE_FOLDER)
    except Exception:
        try:
            await imap.uid('copy', str(uid), 'Archive')
        except Exception:
            pass
    # Best-effort flag deletion + EXPUNGE
    try:
        await imap.uid('store', str(uid), '+FLAGS.SILENT', '(\\Deleted)')
        await imap.expunge()
    except Exception:  # pragma: no cover
        logger.debug("Deletion/expunge not supported for UID %s", uid, exc_info=True)


async def process_loop_async(
    imap,
    poll_interval: int,
    once: bool,
    start_uid: Optional[int],
    stop_event: Optional[asyncio.Event] = None,
    processed_uids: Optional[Set[int]] = None,
) -> int | None:
    """Primary async fetch / dispatch loop.

    Strategy per iteration:
        1. SEARCH for new UIDs (range > last_seen)
        2. For each UID fetch extended data (attempt X-GM attrs) else RFC822
        3. Invoke the provided handler (executed in a thread if it is sync)
        4. (Optional) Archive processed message if AUTO_ARCHIVE_PROCESSED!=0

    Returns last processed UID on reconnect request else None.
    """
    last_seen = start_uid
    if processed_uids is None:
        processed_uids = set()
    tasks: list[asyncio.Task] = []
    while True:
        tasks = [t for t in tasks if not t.done()]
        if tasks:
            logger.info("Have %d email tasks running", len(tasks))
        if stop_event and stop_event.is_set():
            break
        new_uids = await fetch_new_uids_async(imap, last_seen)
        if new_uids:
            logger.info("Found new messages: %s", new_uids)
            for uid in new_uids:
                if uid not in processed_uids:
                    tasks.append(asyncio.create_task(handle_uid_async(imap, uid)))
                processed_uids.add(uid)
                last_seen = uid
        if once:
            break
        # Avoid unnecessary delay in test/once paths; only sleep when continuing
        if poll_interval > 0:
            await asyncio.sleep(poll_interval)
    if tasks:
        logger.info("Stop event set; waiting for %d email tasks to complete", len(tasks))
        await asyncio.gather(*tasks)
    return last_seen


async def run_watcher_async(
    poll_interval: int,
    once: bool,
    start_uid: Optional[int],
    stop_event: Optional[asyncio.Event] = None,
    processed_uids: Optional[Set[int]] = None,
) -> None:
    """Connect and supervise the asynchronous email ingestion loop.

    Handles reconnects on any raised exception from :func:`process_loop_async`.
    ``start_uid`` (if provided) is carried across reconnect attempts so the
    watcher never re-processes older mail. Set ``once=True`` for test / batch
    scenarios: it will process any *currently unseen* UIDs then exit.
    """
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            imap = await ia.connect_imap_async(
                host=eu.IMAP_HOST,
                port=eu.IMAP_PORT,
                username=eu.GMAIL_USER,
                password=eu.GMAIL_PASS,
            )
            folder = eu.IMAP_FOLDER
            sel = await imap.select(folder)
            if getattr(sel, 'result', 'OK') != 'OK':
                raise RuntimeError(f"Failed to select folder {folder}: {getattr(sel, 'result', None)}")
            start_uid = await process_loop_async(
                imap,
                poll_interval,
                once,
                start_uid,
                stop_event=stop_event,
                processed_uids=processed_uids,
            )
        except Exception as e:  # pragma: no cover
            logger.error("Watcher error: %s", e, exc_info=True)
            await asyncio.sleep(3)
            continue
        if once:
            break


async def run_email_watcher(args: argparse.Namespace, stop_event: asyncio.Event):
    """Launch the async email watcher until ``stop_event`` is set.

    Reads IMAP / Gmail credentials from environment. If credentials are
    missing the watcher is skipped (printed to stderr) allowing combined
    invocation with the Notion watcher without failing the whole process.
    """
    if not eu.GMAIL_USER or not eu.GMAIL_PASS:
        logger.warning("Email watcher skipped: missing GMAIL_USER/GMAIL_PASS")
        return
    await run_watcher_async(
        poll_interval=args.poll_interval,
        once=False,
        start_uid=args.email_since,
        stop_event=stop_event,
    )
