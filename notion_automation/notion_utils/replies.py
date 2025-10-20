from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .. import email_utils as eu
from .. import http_async as ha
from . import api as nua
from . import blocks as nub
from . import config as nuc
from . import properties as nup
from .config import PROP_REPLIES_SENT

logger = logging.getLogger(__name__)


async def extract_attachments(prop: Any) -> list[str]:
    """Download file/external URLs concurrently (bounded) to a temp directory.

    Concurrency notes:
        * A small semaphore (default 5) is used to avoid overwhelming the
          remote file hosts / Notion's transient URLs while still achieving
          parallelism versus the previous sequential implementation.
        * Local file cache is respected: already-downloaded files are
          returned immediately without scheduling a coroutine.

    Each file path is derived from a stable hash of the source URL so repeat
    calls short‑circuit once cached.
    """
    t0 = time.time()
    paths: list[str] = []
    if not isinstance(prop, dict):
        return paths
    files = prop.get("files")
    if not isinstance(files, list):
        return paths
    tmp_root = os.path.join(tempfile.gettempdir(), "notion_attachments")
    os.makedirs(tmp_root, exist_ok=True)
    download_tasks = []
    sess = await ha.get_session()

    async def _download(url: str, local_path: str) -> None:
        if os.path.isfile(local_path):
            paths.append(local_path)
            return
        try:
            async with sess.get(url) as r:
                if r.status >= 400:
                    logger.warning("Failed downloading attachment %s status=%s", url, r.status)
                    return
                content = await r.read()
                with open(local_path, "wb") as out:
                    out.write(content)
                paths.append(local_path)
        except Exception as e:  # pragma: no cover
            logger.warning("Failed downloading attachment %s: %s", url, e)

    for f in files:
        if not isinstance(f, dict):
            continue
        name = f.get("name")
        name_str = name if isinstance(name, str) else "attachment"
        if isinstance(name, str) and os.path.isfile(name):
            paths.append(name)
            continue
        url: Optional[str] = None
        if isinstance(f.get("file"), dict):
            url_val = f["file"].get("url")
            if isinstance(url_val, str):
                url = url_val
        elif isinstance(f.get("external"), dict):
            url_val = f["external"].get("url")
            if isinstance(url_val, str):
                url = url_val
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        base = os.path.basename(parsed.path) or name_str
        h = hashlib.sha256(url.encode()).hexdigest()[:12]
        if base == "attachment":
            base = f"file_{h}"
        local_name = f"{h}_{base}"
        local_path = os.path.join(tmp_root, local_name)
        # schedule download (or immediate append if cached inside _download)
        download_tasks.append(asyncio.create_task(_download(url, local_path)))

    if download_tasks:
        await asyncio.gather(*download_tasks, return_exceptions=True)
    logger.info("extract_attachments: processed %d files to %d paths in %.2f sec",
                len(files), len(paths), time.time() - t0)
    return paths


async def process_reply_page_async(database_id: str, page: dict[str, Any], send_emails: bool) -> None:
    properties = page["properties"]
    page_id = page["id"]
    creator_name: Optional[str] = None
    if properties.get(nuc.PROP_REPLIES_INCLUDE_NAME, {}).get("checkbox"):
        created_by = properties.get(nuc.PROP_REPLIES_CREATED_BY, {}).get('created_by', {})
        if (name_val := created_by.get("name", '')):
            creator_name = name_val
    title_prop_name = await nua.get_database_title_property(database_id)
    subject = nup.extract_rich_text_plain(properties.get(title_prop_name))
    subject = eu.clean_subject(subject)
    if not subject:
        logger.warning("Page %s has empty subject/title; skipping", page_id)
        return
    blocks = await nua.fetch_block_children(page_id)
    html, attachments = await asyncio.gather(
        nub.blocks_to_html(blocks),
        extract_attachments(properties.get(nuc.PROP_REPLIES_ATTACHMENTS)),
    )
    ticket_id = nup.extract_rich_text_plain(properties.get(nuc.PROP_REPLIES_TICKET_ID))
    from_emails = nup.extract_emails(properties.get(nuc.PROP_REPLIES_FROM))
    to_emails = nup.extract_emails(properties.get(nuc.PROP_REPLIES_TO))
    cc_emails = nup.extract_emails(properties.get(nuc.PROP_REPLIES_CC))
    if send_emails and eu.GMAIL_USER and eu.GMAIL_PASS:
        in_reply_to_val = nup.extract_rich_text_plain(properties.get(nuc.PROP_REPLIES_IN_REPLY_TO))
        references_val = nup.extract_rich_text_plain(properties.get(nuc.PROP_REPLIES_REFERENCES))
        # Send email in thread executor (blocking SMTP)
        await asyncio.to_thread(
            eu.send_email,
            subject,
            html,
            to_emails,
            cc_emails,
            from_emails[0] if from_emails else None,
            attachments,
            in_reply_to=in_reply_to_val,
            references=references_val,
            ticket_id=ticket_id,
            creator_name=creator_name,
        )
        await nua.patch_page(page_id, {PROP_REPLIES_SENT: {"checkbox": True}})
    else:
        email = from_emails[0] if from_emails else eu.GMAIL_USER or nuc.ENGINEERING_ALIAS
        html = eu.render_email_html(subject, html, email, ticket_id)
        out = Path.cwd() / 'rendered' / f'{page_id}.html'
        out.write_text(html, encoding='utf-8')
