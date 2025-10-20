"""Central logging configuration for notion_automation.

Provides a single configure_logging function to avoid duplicate logic across
modules. Safe to call multiple times; only configures root handlers once.
"""
from __future__ import annotations

import logging
import os

# Include filename:lineno for easier debugging
DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d - %(message)s"


def configure_logging(verbose: bool | None = None) -> None:
    """Configure the root logger once and optionally adjust level.

    This function is idempotent regarding handler creation: the first call
    configures basicConfig with a single stream handler. Subsequent calls will
    only adjust the log level when ``verbose`` is explicitly provided.

    Args:
        verbose: If True force DEBUG level. If False, set level from ``LOG_LEVEL``
            environment variable (default INFO). If None, only configure the
            logger on the first call and leave existing level unchanged.
    """
    # Only add handlers once to prevent duplicate log lines.
    root = logging.getLogger()
    if not root.handlers:
        level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        base_level = getattr(logging, level_name, logging.INFO)
        if verbose is True:
            level = logging.DEBUG
        elif verbose is False:
            level = base_level
        else:
            level = base_level
        logging.basicConfig(level=level, format=DEFAULT_FORMAT)
    else:
        # Adjust level dynamically if verbose flag provided.
        if verbose is True:
            root.setLevel(logging.DEBUG)
        elif verbose is False:
            level_name = os.getenv("LOG_LEVEL", "INFO").upper()
            base_level = getattr(logging, level_name, logging.INFO)
            root.setLevel(base_level)
