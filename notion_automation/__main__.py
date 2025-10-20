"""Async entrypoint orchestrating the Notion + Email watchers.

Replaces the former thread-based supervisor with a pure asyncio
implementation. Live reload (``--reload``) is implemented via polling the
modification times of ``.py`` files and triggering a controlled restart of
all watcher tasks. Shutdown is graceful on SIGINT/SIGTERM: tasks are
cancelled and awaited with a short timeout.

Examples:
    python -m notion_automation --email
    python -m notion_automation --notion
    python -m notion_automation --email --notion --reload
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import List, Optional

from . import watch_email as we
from . import watch_notion as wn
from .asyncio import run_async
from .logging_utils import configure_logging
from .notion_utils.deploy import notion_deploy_async

logger = logging.getLogger(__name__)


def _deep_reload_package(pkg_name: str = 'notion_automation') -> None:
    """Deep reload all loaded submodules of a package.

    Ensures that edits in any submodule (e.g. notion_automation.notion_utils)
    are reflected without restarting the interpreter. Order: reload leaf
    modules before parents to reduce attribute stale refs.
    """
    importlib.invalidate_caches()
    to_reload = [n for n in sys.modules if n == pkg_name or n.startswith(pkg_name + '.')]
    # Sort longest first so children reload before parent
    for name in sorted(to_reload, key=len, reverse=True):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
        except Exception:  # pragma: no cover - best effort
            logger.exception("Failed reloading module %s", name)


def _snapshot_py_files(root: Path) -> dict[str, float]:
    """Return mapping of python file path -> mtime for all project .py files.

    Skips __pycache__ directories. Errors accessing individual files are ignored.
    """
    out: dict[str, float] = {}
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            out[str(path)] = path.stat().st_mtime
        except FileNotFoundError:
            continue
    return out


def _detect_changes(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """Return list of changed / added / removed file paths."""
    changed: list[str] = []
    before_keys = set(before)
    after_keys = set(after)
    # Added / removed
    for p in before_keys ^ after_keys:
        changed.append(p)
    # Modified
    for p in before_keys & after_keys:
        if before[p] != after[p]:
            changed.append(p)
    return sorted(changed)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level orchestrator parser."""
    p = argparse.ArgumentParser(description="Run email watcher, notion watcher, or both.")
    p.add_argument("--email", action="store_true", help="Run the email watcher")
    p.add_argument("--notion", action="store_true", help="Run the notion replies watcher")
    p.add_argument(
        "--email-since", type=int, help="Start email watcher from this UID (exclusive)"
    )
    p.add_argument(
        "--notion-updated-since",
        dest="notion_updated_since",
        help="ISO timestamp for notion watcher start",
    )
    p.add_argument(
        "--notion-send-emails",
        action="store_true",
        help="Enable outbound email send for notion watcher",
    )
    p.add_argument("--verbose", action="store_true", help="Verbose logging (DEBUG level)")
    p.add_argument(
        "--reload",
        action="store_true",
        help=(
            "Enable live reload: monitor python files and automatically restart watchers"
        ),
    )
    p.add_argument(
        "--reload-interval",
        type=float,
        default=1.0,
        help="Polling interval (seconds) for --reload (default: 1.0)",
    )
    p.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL", "10")),
        help="Polling interval (seconds) for watchers (default: 10)",
    )
    p.add_argument(
        '--check-notion-schema', action='store_true',
        help='Perform a Notion database schema audit',
    )
    return p


async def _run_with_reload(args: argparse.Namespace) -> int:
    """Run selected watchers with optional live reload loop.

    When ``--reload`` is enabled, Python source files under the package are
    polled for mtime changes; on change all active watcher tasks are cancelled
    and restarted (unless shutdown was requested via SIGINT/SIGTERM).

    Returns:
        Exit status code (0 on normal shutdown).
    """
    project_root = Path(__file__).resolve().parent
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    interrupt_state = {"count": 0}

    def _sig_handler() -> None:  # first Ctrl+C
        interrupt_state["count"] += 1
        if interrupt_state["count"] == 1:
            stop_event.set()
            logger.info("Graceful shutdown requested (Ctrl+C again to force)")
        else:
            logger.error("Forced exit triggered")
            os._exit(1)

    for sig in (signal.SIGINT, signal.SIGTERM):  # pragma: no cover for signals
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            pass

    async def launch_watchers() -> List[asyncio.Task]:
        tasks: List[asyncio.Task] = []
        if args.email:
            tasks.append(asyncio.create_task(we.run_email_watcher(args, stop_event), name="email"))
        if args.notion:
            tasks.append(asyncio.create_task(wn.run_notion_watcher(args, stop_event), name="notion"))
        return tasks

    async def cancel_tasks(tasks: List[asyncio.Task]) -> None:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def changed_files(prev: dict[str, float]) -> bool:
        snap = _snapshot_py_files(project_root)
        ch = _detect_changes(prev, snap)
        return bool(ch)

    if args.check_notion_schema:
        await notion_deploy_async()
    reload_enabled = args.reload
    interval = max(0.2, float(args.reload_interval)) if reload_enabled else None
    last_snap = _snapshot_py_files(project_root) if reload_enabled else {}

    while True:
        if stop_event.is_set():
            break
        if reload_enabled:
            _deep_reload_package()
        tasks = await launch_watchers()
        # Poll loop
        try:
            while not all(t.done() for t in tasks):
                if stop_event.is_set():
                    break
                await asyncio.sleep(interval if interval is not None else 0.5)
                if reload_enabled and changed_files(last_snap):
                    logger.info("Source change detected -> restarting watchers")
                    stop_event.set()
                    break
        finally:
            await cancel_tasks(tasks)
        if reload_enabled and stop_event.is_set():
            # Prepare for restart unless global shutdown requested
            if interrupt_state["count"] == 0:  # triggered by file change
                stop_event.clear()
                last_snap = _snapshot_py_files(project_root)
                continue
        break
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for module execution.

    Parses arguments, ensures at least one watcher is selected, configures
    logging, and delegates to the async supervisor.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    return run_async(_run_with_reload(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
