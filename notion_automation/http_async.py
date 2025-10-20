"""Async HTTP utilities with shared aiohttp session and minimal retry/backoff.

Centralizes all outbound HTTP for Notion (and optionally image/attachment fetch).
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Iterable, Optional
from venv import logger

import aiohttp
from multidict import MultiMapping

from notion_automation.types import JSON

__all__ = [
    "get_session",
    "close_session",
    "request_json",
]

_SESSION: Optional[aiohttp.ClientSession] = None
_SESSION_LOCK = asyncio.Lock()
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60)


async def get_session() -> aiohttp.ClientSession:
    """Return (and lazily create) the shared aiohttp ClientSession.

    Creates a single process-wide session with a default total timeout. Reuses
    the same session across calls until explicitly closed via ``close_session``.
    Thread‑safe under asyncio through an ``asyncio.Lock``.

    Returns:
        A live ``aiohttp.ClientSession`` instance.
    """
    global _SESSION
    if _SESSION and not _SESSION.closed:
        return _SESSION
    async with _SESSION_LOCK:
        if _SESSION and not _SESSION.closed:
            return _SESSION
        _SESSION = aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT)
    return _SESSION


async def close_session() -> None:
    """Close and reset the shared session if it exists.

    Safe to call multiple times. After closing, the next ``get_session`` call
    will create a fresh session.
    """
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
    _SESSION = None


class NoArgument:
    pass


async def request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Any = None,
    params: Optional[Dict[str, Any]] = None,
    expected: Iterable[int] | None = None,
    max_retries: int = 5,
    retry_on: Iterable[int] = (429, 500, 502, 503, 504),
    backoff_base: float = 0.5,
    default: Any = NoArgument,
) -> JSON:
    """Perform an HTTP request returning JSON with retry/backoff.

    Raises aiohttp.ClientResponseError for non-expected status codes outside retry policy.
    """
    sess = await get_session()
    attempt = 0
    expected_set = set(expected) if expected else set(range(200, 300))
    retry_set = set(retry_on)
    last_err: Exception | None = None
    while True:
        try:
            async with sess.request(method.upper(), url, headers=headers, json=json, params=params) as resp:
                if resp.status in expected_set:
                    if resp.content_type == 'application/json':
                        return await resp.json()
                    text = await resp.text()
                    return {"_text": text, "status": resp.status}
                # Non-retryable
                body = await resp.text()
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=f"Unexpected status {resp.status}: {body[:300]}",
                    headers=resp.headers,
                )
        except aiohttp.ClientResponseError as e:  # network error; possibly retry
            if not (e.status in retry_set and attempt < max_retries):
                logger.warning("Request to %s failed after %d attempts: %s", url, attempt, e)
                if default is not NoArgument:
                    logger.info("Returning default value %s due to request failure.", default)
                    return default
                raise
            header: MultiMapping[str] | dict[str, str] = e.headers or {}
            retry_after = float(header.get("Retry-After", "0") or 0)
            delay = max(retry_after, backoff_base * (2 ** attempt) + random.random() * 0.3)
            await asyncio.sleep(delay)
            attempt += 1
    # Unreachable, but keeps type checker happy
    if last_err:
        raise last_err
    return {}
