"""Shared Notion utility helpers to avoid duplication.

Centralizes:
  - Header construction (required + optional forms)
  - Database query wrapper (simple pagination)
  - Block -> HTML conversion (subset used by email rendering)
  - Property extraction helpers (emails, attachments)

The goal is to let both `watch_notion` and `watch_email` reuse these primitives
without each redefining them.
"""

from . import api, config
from .blocks import blocks_to_html
from .email import build_email_content_blocks, create_email_record
from .html import _rt, html_to_blocks
from .replies import process_reply_page_async
from .support_case import (extract_ticket_id, find_or_create_support_case,
                           find_support_case)

__all__ = [
    "_rt",
    "find_support_case",
    "find_or_create_support_case",
    "extract_ticket_id",
    "create_email_record",
    "process_reply_page_async",
    "build_email_content_blocks",
    "config",
    "api",
    'blocks_to_html',
    'html_to_blocks',
]
