from __future__ import annotations

import asyncio
import imaplib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class IMAPResponse:
    """Simple response shim mimicking aioimaplib's minimal attributes.

    Attributes:
        result: "OK" or "NO" style status.
        lines: list of raw byte lines composing the response payload.
    """

    def __init__(self, result: str, lines: list[bytes]):
        self.result = result
        self.lines = lines

    def __repr__(self):
        return f"IMAPResponse(result={self.result})"


def _to_response(typ: str, data: list[Any]):
    result = typ
    lines = []
    for d in data or []:
        if isinstance(d, bytes):
            lines.append(d)
        elif isinstance(d, str):
            lines.append(d.encode())
        elif isinstance(d, tuple):  # sometimes fetch returns nested tuples
            for sub in d:
                if isinstance(sub, bytes):
                    lines.append(sub)
                elif isinstance(sub, str):
                    lines.append(sub.encode())
    res = IMAPResponse(result, lines)
    logger.debug("Got response: %s", res)
    return res


class AsyncImapClient:
    """Async wrapper around blocking ``imaplib.IMAP4_SSL``.

    Exposes ``select`` and ``uid`` as coroutines returning objects with
    ``result`` and ``lines`` attributes to minimize changes to the rest
    of the watcher logic.
    """

    def __init__(self, host: str, port: int):
        # imaplib does its own SSL negotiation inside IMAP4_SSL
        self._client = imaplib.IMAP4_SSL(host=host, port=port)
        self._lock = asyncio.Lock()

    async def login(self, username, password):  # idempotent
        # login is blocking; perform in thread
        await asyncio.to_thread(self._client.login, username, password)

    async def select(self, mailbox: str) -> IMAPResponse:
        logger.debug("Selecting mailbox: %s", mailbox)
        resp = await asyncio.to_thread(self._client.select, mailbox)
        return _to_response(*resp)

    async def uid(self, command: str, *args: str) -> IMAPResponse:
        async with self._lock:
            logger.debug("IMAP UID command: %s %s", command, args)
            resp = await asyncio.to_thread(self._client.uid, command, *args)
        return _to_response(*resp)

    async def expunge(self) -> IMAPResponse:
        async with self._lock:
            logger.debug("IMAP EXPUNGE command")
            resp = await asyncio.to_thread(self._client.expunge)
        # imaplib.expunge returns (typ, data)
        return _to_response(*resp)


async def connect_imap_async(host: str, port: int, username: str, password: str):
    """Async connection helper using standard library imaplib via AsyncImapClient."""
    imap = AsyncImapClient(host=host, port=port)
    await imap.login(username, password)
    return imap
