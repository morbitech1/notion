"""Notion replies database watcher.

Polls a configured Notion database for pages marked with a *send email* checkbox
and optionally dispatches formatted emails via Gmail SMTP, then marks pages as
sent. Designed as a lightweight long-running process.
"""
import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import notion_utils as nu

logger = logging.getLogger(__name__)


REPLIES_DATABASE_ID = os.getenv("NOTION_REPLIES_DB_ID", '')


def iso_now() -> str:
    """Return current UTC timestamp in Notion-friendly ISO8601 (Z) format.

    Returns:
        Timestamp like ``2025-09-28T12:34:56Z``.
    """
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


async def watch_database_async(
    poll_interval: int,
    initial_updated_since: Optional[str],
    send_emails: bool,
    stop_event: asyncio.Event
) -> None:
    send = nu.config.PROP_REPLIES_SEND
    """Async continuous poll of replies database to send emails."""
    logger.info("Watching database %s for checkbox '%s' starting at >= %s",
                REPLIES_DATABASE_ID, send, initial_updated_since or "BEGINNING")
    last_checked: Optional[str] = initial_updated_since
    processed_pages: Dict[str, tuple[str, asyncio.Task]] = {}
    tasks: list[asyncio.Task] = []

    async def watch_loop() -> Optional[str]:
        payload: Dict[str, Any] = {
            "page_size": 100,
            "sorts": [
                {"timestamp": "last_edited_time", "direction": "ascending"}
            ]
        }
        if last_checked:
            payload["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": last_checked}
            }
        data = await nu.api.query_database(REPLIES_DATABASE_ID, payload)
        max_seen = last_checked
        results = data.get("results", [])
        for page in results:
            page_id = page.get("id") if isinstance(page.get("id"), str) else None
            if not page_id:
                continue
            properties = page.get("properties", {})
            last_edit = page.get("last_edited_time", '')
            if last_edit and (max_seen is None or last_edit > (max_seen or "")):
                max_seen = last_edit
            try:
                should_send = properties[send]["checkbox"]
                already_sent = properties[nu.config.PROP_REPLIES_SENT]["checkbox"]
            except Exception:
                continue
            if not should_send or already_sent:
                continue
            if t := processed_pages.get(page_id):
                last_edit_page, task = t
                if last_edit_page == last_edit:
                    continue
                if not task.done():
                    continue
            task = asyncio.create_task(nu.process_reply_page_async(REPLIES_DATABASE_ID, page, send_emails))
            tasks.append(task)
            if last_edit:
                processed_pages[page_id] = (last_edit, task)
        return max_seen

    while True:
        tasks = [t for t in tasks if not t.done()]
        if tasks:
            logger.info("Have %d email tasks running", len(tasks))
        if stop_event and stop_event.is_set():
            break
        try:
            last_checked = await watch_loop()
        except Exception as e:
            logger.error("Error in watch loop: %s", e, exc_info=True)
        await asyncio.sleep(poll_interval)
    if tasks:
        logger.info("Stop event set; waiting for %d email tasks to complete", len(tasks))
        await asyncio.gather(*tasks)


async def run_notion_watcher(args: argparse.Namespace, stop_event: asyncio.Event):
    """Launch the async Notion replies watcher until ``stop_event`` is set.

    Skips execution if required environment variables are absent so that a
    single command can start both watchers gracefully.
    """
    updated_since = args.notion_updated_since or iso_now()
    await watch_database_async(
        initial_updated_since=updated_since,
        send_emails=args.notion_send_emails,
        stop_event=stop_event,
        poll_interval=args.poll_interval,
    )
