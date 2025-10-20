"""Shared Notion utility helpers to avoid duplication.

Centralizes:
  - Header construction (required + optional forms)
  - Database query wrapper (simple pagination)
  - Block -> HTML conversion (subset used by email rendering)
  - Property extraction helpers (emails, attachments)

The goal is to let both `watch_notion` and `watch_email` reuse these primitives
without each redefining them.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from notion_automation.types import JSON, Pages

from .. import http_async as ha
from . import config as nuc

logger = logging.getLogger(__name__)


async def query_database(database_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Pages]:
    """Query a Notion database and return aggregated results (auto-pagination).

    Performs repeated POST queries following ``next_cursor`` until ``has_more``
    is False. A shallow copy of ``payload`` is used so the caller's object
    remains unmodified when pagination state is injected.

    Args:
        database_id: Target database UUID (hyphenated or not).
        payload: Optional base query JSON (``filter``, ``sorts`` etc.). ``page_size``
            is defaulted to 100 when absent.

    Returns:
        Dict with single key ``"results"`` mapping to the full list of page objects.
    """
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    body: Dict[str, Any] = payload.copy() if payload else {}
    if "page_size" not in body:
        body["page_size"] = 100
    all_results: Pages = []
    while True:
        data = await ha.request_json("POST", url, headers=nuc.HEADERS, json=body)
        all_results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data.get("next_cursor")
    return {"results": all_results}


async def fetch_block_children(
    block_id: str,
    page_size: int = 100,
    parent: Optional[JSON] = None,
) -> List[JSON]:
    """Return children blocks for a given block id with simple pagination.

    Args:
        block_id: Notion block identifier.
        page_size: Per-page size (1–100).

    Returns:
        List of block JSON objects (may be empty on error).
    """
    results: List[JSON] = []
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    params: Dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
    while True:
        try:
            sess = await ha.get_session()
            async with sess.get(url, headers=nuc.HEADERS, params=params) as resp:
                if resp.status >= 400:
                    body = (await resp.text())[:200]
                    logger.warning("Failed fetching block children %s: %s %s", block_id, resp.status, body)
                    break
                data = await resp.json()
        except Exception as e:  # pragma: no cover
            logger.warning("Exception fetching block children %s: %s", block_id, e)
            break
        new_results = data.get("results")
        if isinstance(new_results, list):
            results.extend(new_results)
        if not data.get("has_more"):
            break
        next_cursor = data.get("next_cursor")
        if not next_cursor:
            break
        params["start_cursor"] = next_cursor
    await asyncio.gather(*[fetch_block_children(r['id'], parent=r) for r in results if r.get('has_children')])
    if parent:
        ptype = parent.get("type", {})
        parent[ptype]['children'] = results
    return results


async def create_page(
    database_id: str,
    properties: Dict[str, Any],
    children: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Create a new page in a database.

    Args:
        database_id: Parent database id.
        properties: Property map conforming to Notion API schema.
        children: Optional list of block objects to append on creation.

    Returns:
        New page id (string) or None on failure.
    """
    url = "https://api.notion.com/v1/pages"
    payload: Dict[str, Any] = {"parent": {"database_id": database_id}, "properties": properties}
    if children:
        # Transform table + subsequent table_row siblings into embedded children
        transformed: List[Dict[str, Any]] = []
        i = 0
        while i < len(children):
            blk = children[i]
            if isinstance(blk, dict) and blk.get("type") == "table" and isinstance(blk.get("table"), dict):
                tbl = blk["table"]
                # Ensure children key exists
                if "children" not in tbl or not isinstance(tbl.get("children"), list):
                    tbl["children"] = []
                j = i + 1
                # Collect consecutive table_row blocks that follow
                while j < len(children):
                    nxt = children[j]
                    if not (isinstance(nxt, dict)
                            and nxt.get("type") == "table_row"
                            and isinstance(nxt.get("table_row"), dict)):
                        break
                    tbl["children"].append(nxt)
                    j += 1
                transformed.append(blk)
                i = j
                continue
            transformed.append(blk)
            i += 1
        payload["children"] = transformed
    data = await ha.request_json("POST", url, headers=nuc.HEADERS, json=payload, default={})
    pid = data.get("id")
    return pid if isinstance(pid, str) else None


async def list_database_pages(database_id: str, page_size: int = 50) -> List[JSON]:
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    body = {"page_size": page_size}
    data = await ha.request_json("POST", url, headers=nuc.HEADERS, json=body, default={})
    results = data.get("results")
    return results if isinstance(results, list) else []


async def patch_page(page_id: str, properties: JSON) -> bool:
    """Async set the sent checkbox on a page (errors logged)."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": properties}
    try:
        await ha.request_json("PATCH", url, headers=nuc.HEADERS, json=payload)
        logger.info("Patched page %s property with properties '%s'", page_id, properties)
        return True
    except Exception as e:
        logger.warning("Exception patching page %s: %s", page_id, e)
        return False

_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


async def fetch_database_schema(database_id: str) -> Dict[str, Any]:
    """Fetch and cache raw database schema JSON for a database id."""
    if database_id in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[database_id]
    url = f"https://api.notion.com/v1/databases/{database_id}"
    data = await ha.request_json("GET", url, headers=nuc.HEADERS, default={})
    _SCHEMA_CACHE[database_id] = data
    return data


async def get_database_title_property(database_id: str) -> Optional[str]:
    """Return the title property name for a database or None if undetermined."""
    data = await fetch_database_schema(database_id)
    props = data.get("properties") if isinstance(data, dict) else None
    if isinstance(props, dict):
        for n, v in props.items():
            if isinstance(v, dict) and v.get("type") == "title" and isinstance(n, str):
                return n
    return None


async def get_database_property_type(database_id: str, property_name: str) -> Optional[str]:
    """Return the Notion internal type for a property name (async)."""
    if not property_name or not database_id:
        return None
    data = await fetch_database_schema(database_id)
    props = data.get("properties", {})
    prop_obj = props.get(property_name)
    return prop_obj.get("type")


async def patch_database_properties(database_id: str, properties: Dict[str, Any]) -> bool:
    """Async database properties patch; returns True on success."""
    if not properties:
        return True
    url = f"https://api.notion.com/v1/databases/{database_id}"
    payload = {"properties": properties}
    try:
        await ha.request_json("PATCH", url, headers=nuc.HEADERS, json=payload)
        logger.info("Patched database %s properties=%s", database_id, list(properties.keys()))
        return True
    except Exception as e:  # pragma: no cover
        logger.debug("patch async exception %s: %s", database_id, e)
        return False


async def create_database_async(
    parent_page_id: str,
    title: str,
    properties: Dict[str, Any],
    icon_emoji: Optional[str] = None,
) -> JSON:
    """Create a new Notion database under a parent page.

    Args:
        parent_page_id: The page ID under which to create the database.
        title: Database title.
        properties: Initial properties map (must include at least one title property).

    Returns:
        New database id or None on failure.
    """
    url = "https://api.notion.com/v1/databases"
    payload: Dict[str, Any] = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties,
    }
    if icon_emoji:
        payload["icon"] = {"type": "emoji", "emoji": icon_emoji}
    return await ha.request_json("POST", url, headers=nuc.HEADERS, json=payload, default={})


async def create_workspace_page_async(title: str) -> JSON:
    """Create a top-level (workspace root) page and return its id.

    Used as a parent container for auto-created databases when NOTION_PARENT_PAGE_ID
    is not provided.
    """
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"type": "workspace", "workspace": True},
        "properties": {
            "title": [
                {"type": "text", "text": {"content": title}}
            ]
        }
    }
    return await ha.request_json("POST", url, headers=nuc.HEADERS, json=payload, default={})
