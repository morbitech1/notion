import asyncio
from typing import Any

from notion_automation.http_async import close_session


def run_async(coro) -> Any:
    """Run an async coroutine from sync code, handling event loop issues."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    async def run_and_close():
        try:
            return await coro
        finally:
            await close_session()
    if loop and loop.is_running():
        # If there's a running loop, create a new one in a separate thread
        return asyncio.run_coroutine_threadsafe(run_and_close(), loop).result()
    else:
        return asyncio.run(run_and_close())
