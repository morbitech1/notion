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
import html as html_lib
import logging
import time
from typing import Any, Iterable, Optional, Sequence

from notion_automation.types import JSON

from .. import s3_utils as su

logger = logging.getLogger(__name__)


def build_text(r: dict[str, Any]) -> str:
    t = r.get("type")
    text_obj = r.get('text', {})
    link_meta = text_obj.get('link', {})
    pt = text_obj.get('content', '')
    if not pt:
        pt = r.get('plain_text')
    if not pt:
        return ''
    seg_html = html_lib.escape(pt)
    ann = r.get("annotations", {}) if isinstance(r.get("annotations"), dict) else {}
    if ann.get("code") and t != "code":
        seg_html = f"<code>{seg_html}</code>"
    else:
        wraps: list[str] = []
        if ann.get("bold"):
            wraps.append("strong")
        if ann.get("italic"):
            wraps.append("em")
        if ann.get("underline"):
            wraps.append("u")
        if ann.get("strikethrough"):
            wraps.append("del")
        for w in wraps:
            seg_html = f"<{w}>{seg_html}</{w}>"
    color = ann.get("color") if isinstance(ann.get("color"), str) else "default"
    if color and color != "default":
        cls = f"color-{html_lib.escape(color)}".replace(" ", "-")[:40]
        seg_html = f"<span class=\"{cls}\">{seg_html}</span>"
    link_url = link_meta.get("url") if isinstance(link_meta, dict) else None
    if isinstance(link_url, str) and link_url.startswith("http") and t != "code":
        safe = html_lib.escape(link_url)
        seg_html = f"<a href=\"{safe}\" target=\"_blank\" rel=\"noopener noreferrer\">{seg_html}</a>"
    return seg_html


def build_rich_text(sec: dict[str, Any], t: str | None) -> str:
    rt = sec.get("rich_text")
    if not isinstance(rt, list):
        return ""
    segs_out: list[str] = []
    for r in rt:
        if not isinstance(r, dict):
            continue
        segs_out.append(build_text(r))
    if t == "code":
        return "".join(
            r.get("plain_text", "") for r in rt if isinstance(r, dict)
        )
    return "".join(segs_out)


def join_html(parts: Iterable[str]) -> str:
    res = "".join(parts)
    res = res.replace('</ol><ol>', '')
    res = res.replace('</ul><ul>', '')
    return res


async def render_block(block: JSON) -> str:
    t = block.get("type")
    if t is None:
        return block.get('plain_text', '')
    if t == "divider":
        return ""
    section: dict[str, Any] = block.get(t, {})

    if t == "table":
        rows_src: list[list[str]] = []
        for rblk in block.get(t, {}).get("children", []):
            if (
                isinstance(rblk, dict)
                and rblk.get("type") == "table_row"
                and isinstance(rblk.get("table_row"), dict)
            ):
                rdata = rblk.get("table_row", {})
                all_cells = rdata.get("cells", [])
                out_row: list[str] = []
                for cells in all_cells:
                    out_row.append(join_html(await asyncio.gather(*[render_block(cell) for cell in cells])))
                rows_src.append(out_row)
        if not rows_src:
            return ""
        has_col_header = bool(section.get("has_column_header"))
        has_row_header = bool(section.get("has_row_header"))
        tbl: list[str] = ["<table>"]
        body_rows = rows_src
        if has_col_header and rows_src:
            header = rows_src[0]
            tbl.append("<thead><tr>" + join_html(f"<th>{c}</th>" for c in header) + "</tr></thead>")
            body_rows = rows_src[1:]
        tbl.append("<tbody>")
        for row in body_rows:
            if not row:
                continue
            cell_html: list[str] = []
            for ci, cell in enumerate(row):
                if has_row_header and ci == 0:
                    cell_html.append(f"<th scope=\"row\">{cell}</th>")
                else:
                    cell_html.append(f"<td>{cell}</td>")
            tbl.append("<tr>" + join_html(cell_html) + "</tr>")
        tbl.append("</tbody></table>")
        return join_html(tbl)

    if t == "image":
        url: Optional[str] = None
        if isinstance(section.get("file"), dict):
            url = section.get("file", {}).get("url")
        elif isinstance(section.get("external"), dict):
            url = section.get("external", {}).get("url")
        final_url = url or ""
        if final_url and su.s3_enabled(final_url):
            if mirrored := await su.s3_upload_url(final_url):
                final_url = mirrored
        caption_text = ""
        caption = section.get("caption")
        if isinstance(caption, list):
            cap_parts: list[str] = []
            for c in caption:
                if isinstance(c, dict):
                    ptc = c.get("plain_text")
                    if isinstance(ptc, str):
                        cap_parts.append(ptc)
            caption_text = join_html(cap_parts)
        alt = html_lib.escape(caption_text or "image")
        tag = (
            f"<img src=\"{html_lib.escape(final_url)}\" alt=\"{alt}\" "
            "style=\"max-width:100%;height:auto;display:block;\" />"
        )
        if caption_text:
            tag = (
                f"<figure>{tag}<figcaption>"
                f"{html_lib.escape(caption_text)}</figcaption></figure>"
            )
        return tag

    content = build_rich_text(section, t)
    if t == "paragraph":
        return f"<p>{content}</p>" if content else "<br />"
    if t in {"heading_1", "heading_2", "heading_3"}:
        tag_map = {"heading_1": "h1", "heading_2": "h2", "heading_3": "h3"}
        return f"<{tag_map[t]}>{content}</{tag_map[t]}>"
    if t == "toggle":
        # Render toggle block using email-client friendly markup (avoid <details>/<summary>).
        # Structure example:
        # <div class="toggle"><div class="toggle-summary">Summary text</div>
        #      <div class="toggle-content">children...</div></div>
        # Always expanded (no JS) since most email clients lack env support.
        toggle_section = block.get(t)
        children_html: list[str] = []
        if isinstance(toggle_section, dict):
            for ch in toggle_section.get("children", []) or []:
                if isinstance(ch, dict):
                    children_html.append(await render_block(ch))
        inner = join_html(children_html)
        summary = content or "Details"
        return (
            "<div class=\"toggle\">"
            f"<div class=\"toggle-summary\">{summary}</div>"
            f"<div class=\"toggle-content\">{inner}</div>"
            "</div>"
        )
    tag = ''
    if t == 'bulleted_list_item':
        tag = 'ul'
    if t == 'numbered_list_item':
        tag = 'ol'
    if t in {"bulleted_list_item", "numbered_list_item"}:
        children_rendered = await asyncio.gather(*[render_block(ch) for ch in block.get(t, {}).get("children", [])])
        extra = join_html(children_rendered)
        return f'<{tag}><li>{content}</li>{extra}</{tag}>'
    if t == "quote":
        return f"<blockquote>{content.replace('\n', '<br />')}</blockquote>"
    if t == "callout":
        icon = None
        if isinstance(section.get("icon"), dict) and section["icon"].get("type") == "emoji":
            icon = section["icon"].get("emoji")
        icon_html = (
            f"<span class=\"callout-icon\">{html_lib.escape(icon)}</span> "
            if isinstance(icon, str) and icon else ""
        )
        return f"<div class=\"callout\">{icon_html}{content}</div>"
    if t == "code":
        language = section.get("language") if isinstance(section.get("language"), str) else None
        cls = f" class=\"language-{html_lib.escape(language)}\"" if language else ""
        return f"<pre><code{cls}>{html_lib.escape(content)}</code></pre>"
    if content:
        return f"<p>{content}</p>"
    if t == 'text':
        return build_text(block)
    return ''


def _is_empty_block(b: Any) -> bool:
    if not isinstance(b, dict):
        return True
    t_val = b.get("type")
    t = t_val if isinstance(t_val, str) else None
    if not t:
        return True
    if t == "image":
        sec = b.get("image") if isinstance(b.get("image"), dict) else None
        if not isinstance(sec, dict):
            return True
        if isinstance(sec.get("file"), dict) and isinstance(sec.get("file", {}).get("url"), str):
            return False
        if isinstance(sec.get("external"), dict) and isinstance(sec.get("external", {}).get("url"), str):
            return False
        return True
    if t in {
        "paragraph", "heading_1", "heading_2", "heading_3",
        "quote", "callout", "code", "bulleted_list_item", "numbered_list_item",
    }:
        sec = b.get(t) if isinstance(b.get(t), dict) else None
        if not isinstance(sec, dict):
            return True
        rt = sec.get("rich_text")
        if not isinstance(rt, list) or not rt:
            return True
        for r in rt:
            if isinstance(r, dict):
                pt = r.get("plain_text")
                if isinstance(pt, str) and pt.strip():
                    return False
        return True
    return False


async def blocks_to_html(blocks: Sequence[JSON]) -> str:
    """Render blocks (with potential nested children) to HTML with recursion."""

    # Trim leading/trailing empties
    t0 = time.time()
    start = 0
    end = len(blocks) - 1
    while start <= end and _is_empty_block(blocks[start]):
        start += 1
    while end >= start and _is_empty_block(blocks[end]):
        end -= 1
    blocks = list(blocks[start:end + 1]) if (start != 0 or end != len(blocks) - 1) else list(blocks)
    if (i := next((i for i, b in enumerate(blocks) if b.get('type') == 'divider'), None)) is not None:
        blocks = blocks[:i]
    html_parts = await asyncio.gather(*[render_block(b) for b in blocks])
    res = join_html(html_parts)
    logger.debug("blocks_to_html: rendered %d blocks to %d bytes in %.2f sec", len(blocks), len(res), time.time() - t0)
    return res
