from __future__ import annotations

import asyncio
import logging
import re
import time
from html.parser import HTMLParser
from typing import Any, Awaitable, Dict, List, Optional, Sequence

from .. import s3_utils as su
from . import api as nua

logger = logging.getLogger(__name__)

LANGUAGES = {
    "abap",
    "arduino",
    "bash",
    "basic",
    "c",
    "clojure",
    "coffeescript",
    "c++",
    "c#",
    "css",
    "dart",
    "diff",
    "docker",
    "elixir",
    "elm",
    "erlang",
    "flow",
    "fortran",
    "f#",
    "gherkin",
    "glsl",
    "go",
    "graphql",
    "groovy",
    "haskell",
    "html",
    "java",
    "javascript",
    "json",
    "julia",
    "kotlin",
    "latex",
    "less",
    "lisp",
    "livescript",
    "lua",
    "makefile",
    "markdown",
    "markup",
    "matlab",
    "mermaid",
    "nix",
    "objective-c",
    "ocaml",
    "pascal",
    "perl",
    "php",
    "plain text",
    "powershell",
    "prolog",
    "protobuf",
    "python",
    "r",
    "reason",
    "ruby",
    "rust",
    "sass",
    "scala",
    "scheme",
    "scss",
    "shell",
    "sql",
    "swift",
    "typescript",
    "vb.net",
    "verilog",
    "vhdl",
    "visual basic",
    "webassembly",
    "xml",
    "yaml",
    "java/c/c++/c#",
}


def _rt(text: str) -> List[Dict[str, Any]]:
    """Helper to build a rich_text array with truncated content (<=2000 chars)."""
    truncated = text[:2000]
    return [{
        "type": "text",
        "text": {"content": truncated},
        "plain_text": truncated,
    }]


async def handle_cid_image(
    blocks: List[Dict[str, Any]],
    cid: str,
    cid_image_map: Dict[str, Dict[str, Any]],
    alt: Optional[str],
) -> List[Dict[str, Any]]:
    meta = cid_image_map.get(cid)
    if meta and isinstance(meta.get("data"), (bytes, bytearray)):
        raw_bytes = meta["data"]
        ctype = meta.get("content_type")
        filename = meta.get("filename") or f"inline-{cid}"
        if upload := await nua.upload_file(filename, raw_bytes, ctype):
            blocks.append({
                "object": "block",
                "type": "image",
                "image": upload,
            })
            return blocks
        if su.s3_enabled() and (mirrored := await su.s3_upload(filename[:80], raw_bytes, ctype)):
            final_src = mirrored
        elif len(raw_bytes) <= 40_000:
            import base64
            b64 = base64.b64encode(raw_bytes).decode()
            final_src = f"data:{ctype};base64,{b64}"
        else:
            logger.info("Skipping large inline image cid:%s size=%d", cid, len(raw_bytes))
            return blocks
        if final_src and len(final_src) < 2000:
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {"type": "external", "external": {"url": final_src}},
            })
        elif alt:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rt(alt[:1800])},
            })
    return blocks


async def add_image_block(blocks: list[Any], src: str, alt: Optional[str]) -> list[Any]:
    if uploaded := await nua.upload_file_url(src):
        blocks.append({
            "object": "block",
            "type": "image",
            "image": uploaded,
        })
        return blocks
    if su.s3_enabled(src) and (mirrored := await su.s3_upload_url(src)):
        src = mirrored
    src = src.split("?")[0]  # strip query params
    if len(src) <= 2000:
        blocks.append({
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": src}},
        })
    elif alt:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rt(alt[:1800])},
        })
    return blocks


class SimpleParser(HTMLParser):
    def __init__(self, cid_image_map: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        super().__init__()
        self.blocks: List[Dict[str, Any]] = []
        self.stack: List[str] = []
        self.cur_text: List[str] = []
        self.list_parent_stack: List[Dict[str, Any]] = []  # parent list item blocks for nesting
        self.in_pre: bool = False
        self.code_language: Optional[str] = None  # language captured from <code class="language-...">
        self.image_attrs: List[tuple[str, str]] = []  # (src, alt)
        self.skip_content: bool = False  # inside <style> or <script>
        self.link_stack: List[Optional[str]] = []  # Track href for <a> tags
        self.rich_text_buffer: List[Dict[str, Any]] = []  # Buffer for rich_text objects
        self.current_block: Optional[str] = None  # Track current block context
        self.skip_icon_text: int = 0  # nesting counter for callout icon span
        # Holds active annotations context (bool flags + optional 'color')
        self.format_stack: List[Dict[str, Any]] = []
        # Table parsing state
        self.in_table: bool = False
        self.in_row: bool = False
        self.current_row_cells: List[List[Dict[str, Any]]] = []  # list of cell rich_text lists
        self.current_cell_buffer: List[Dict[str, Any]] = []
        self.table_rows: List[List[List[Dict[str, Any]]]] = []  # rows -> cells -> rich_text objects
        self.cell_tag_stack: List[str] = []  # track td/th for header detection
        self.awaitables: List[Awaitable[list[Any]]] = []
        self.cid_image_map = cid_image_map  # cid -> {data, content_type, filename}
        # Toggle parsing support (<div class="toggle"> markup)
        self.toggle_stack: List[Dict[str, Any]] = []  # stack of contexts

    # ---- Rich text helpers ----
    def _merge_active_annotations(self) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        color_val: Optional[str] = None
        for ann in self.format_stack:
            for k, v in ann.items():
                if k == "color" and isinstance(v, str):
                    color_val = v  # last wins
                    continue
                if isinstance(v, bool) and v:
                    merged[k] = True
        if color_val:
            merged["color"] = color_val
        return merged

    def _append_text_segment(
        self,
        text: str,
        *,
        link: Optional[str] = None,
        target: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Append a single rich_text text segment with current active annotations.

        Args:
            text: Raw segment content (will be truncated to 2000 chars).
            link: Optional URL to attach as a link object.
            target: Optional list to append into (defaults to self.rich_text_buffer).
        """
        if not text:
            return
        seg_target = target if target is not None else self.rich_text_buffer
        merged = self._merge_active_annotations()
        payload: Dict[str, Any] = {
            "type": "text",
            "text": {"content": text[:2000]},
            "plain_text": text[:2000],
            "annotations": {
                "bold": bool(merged.get("bold")),
                "italic": bool(merged.get("italic")),
                "strikethrough": bool(merged.get("strikethrough")),
                "underline": bool(merged.get("underline")),
                "code": bool(merged.get("code")),
                "color": merged.get("color") if isinstance(merged.get("color"), str) else "default",
            },
        }
        if link and any(link.startswith(part) for part in ['http://', 'https://', 'mailto:']):
            payload["text"]["link"] = {"url": link}
        seg_target.append(payload)

    # ---- Block emission helpers ----
    def _emit_heading(self, level_tag: str, rich_segments: List[Dict[str, Any]]) -> None:
        htype = {"h1": "heading_1", "h2": "heading_2", "h3": "heading_3"}.get(level_tag)
        if not htype:
            return
        self._append_block({
            "object": "block",
            "type": htype,
            htype: {"rich_text": rich_segments[:50]},
        })

    def _emit_quote(self, rich_segments: List[Dict[str, Any]]) -> None:
        if not rich_segments:
            return
        self._append_block({
            "object": "block",
            "type": "quote",
            "quote": {"rich_text": rich_segments[:50]},
        })

    def _emit_list_item(self, rich_segments: List[Dict[str, Any]]) -> None:
        new_block = {**self.list_parent_stack[-1]}
        btype = new_block.get("type")
        if not btype:
            return
        new_block[btype] = {}
        new_block[btype]["rich_text"] = rich_segments[:50]
        target_blocks = self._target_blocks()
        if len(self.list_parent_stack) == 1:
            target_blocks.append(new_block)
            return
        parent = target_blocks[-1]
        ptype = parent.get("type", '')
        if ptype == btype:
            self.list_parent_stack[-2] = new_block
            target_blocks.append(new_block)
            return
        obj = parent.get(ptype, {})
        obj["children"] = obj.get('children', []) + [new_block]

    def flush_paragraph(self) -> None:
        # Only flush as paragraph if we have content
        if self.rich_text_buffer:
            # If collecting summary, append to summary_rt instead of creating paragraph blocks
            if self.toggle_stack and self.toggle_stack[-1].get("collecting_summary"):
                self.toggle_stack[-1]["summary_rt"].extend(self.rich_text_buffer[:50])
                self.rich_text_buffer = []
                self.cur_text = []
                return
            target_blocks = self._target_blocks()
            target_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": self.rich_text_buffer[:50]},
            })
            self.rich_text_buffer = []
            self.cur_text = []
            return
        raw = "".join(self.cur_text)
        raw = raw.lstrip("\n")  # remove leading solitary newlines
        text = raw.strip()
        self.cur_text = []
        if not text:
            return
        parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        for ptxt in parts[:50]:
            target_blocks = (
                self.blocks
                if (not self.toggle_stack or self.toggle_stack[-1].get("collecting_summary"))
                else self.toggle_stack[-1]["children"]
            )
            target_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rt(ptxt[:1800])},
            })
    # Helper to choose correct append list depending on toggle context

    def _target_blocks(self) -> List[Dict[str, Any]]:
        if self.toggle_stack and not self.toggle_stack[-1].get("collecting_summary"):
            return self.toggle_stack[-1]["children"]
        return self.blocks

    def _append_block(self, block: Dict[str, Any]) -> None:
        self._target_blocks().append(block)

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        self.stack.append(tag)
        src: Optional[str] = None
        alt: Optional[str] = None
        if tag in ("style", "script"):
            self.skip_content = True
            return
        if tag in ("div", "p"):
            # Detect callout div
            callout_classes = "".join(v for k, v in attrs if k == "class" and isinstance(v, str))
            if tag == "div" and "callout" in callout_classes.split():
                self.current_block = "callout"
            else:
                self.current_block = "paragraph"
        elif tag in ("h1", "h2", "h3"):
            self.current_block = "heading"
        elif tag == "blockquote":
            self.current_block = "quote"
        elif tag in ("ul", "ol"):
            self.current_block = "list"
            btype = "bulleted_list_item" if tag == "ul" else "numbered_list_item"
            self.list_parent_stack.append({
                "object": "block",
                "type": btype,
            })
        elif tag == "li":
            self.current_block = "list_item"
        elif tag == "pre":
            self.current_block = "pre"
            self.in_pre = True
        elif tag == "code" and self.in_pre:
            # Capture language from class attribute if present
            for k, v in attrs:
                if k == "class" and isinstance(v, str) and "language-" in v:
                    # e.g. "language-python" or multiple classes
                    for part in v.split():
                        if part.startswith("language-") and len(part) > 9:
                            self.code_language = part[9:30]
                            break
                    break
        if tag == "div":
            # Flush paragraph before starting a new div block (e.g. callout)
            self.flush_paragraph()
        # For blockquote we defer flushing until closing to allow color spans inside
        if tag == "br":
            # Represent explicit empty paragraph blocks for standalone <br /> tags
            if self.rich_text_buffer or self.cur_text:
                # Inside content: treat as line break
                self.cur_text.append("\n")
            else:
                # Standalone break -> empty paragraph block
                self.blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": []},
                })
        if tag == "span":
            # detect color class (color-red, ...) and add isolated annotation layer
            color: Optional[str] = None
            classes: List[str] = []
            for k, v in attrs:
                if k == "class" and isinstance(v, str):
                    classes += v.split()
            for part in classes:
                if part.startswith("color-") and len(part) > 6:
                    color = part[6:46]
                    break
            if color and color != "default":
                self.format_stack.append({"color": color})
            if "callout-icon" in classes:
                self.skip_icon_text += 1
        # Formatting tags: push annotation context
        if tag in ("strong", "b", "em", "i", "u", "del", "code"):
            ann: Dict[str, bool] = {}
            if tag in ("strong", "b"):
                ann["bold"] = True
            if tag in ("em", "i"):
                ann["italic"] = True
            if tag == "u":
                ann["underline"] = True
            if tag == "del":
                ann["strikethrough"] = True
            if tag == "code" and not self.in_pre:  # inline code
                ann["code"] = True
            self.format_stack.append(ann)
        if tag == "a":
            # Capture href and push onto link stack; segments added while active will carry link
            href: Optional[str] = None
            for k, v in attrs:
                if k == "href" and isinstance(v, str):
                    href = v.strip()
                    break
            self.link_stack.append(href)
        if tag == "img":
            for k, v in attrs:
                if k == "src":
                    src = v
                elif k == "alt":
                    alt = v
            if src and src.startswith("cid:") and self.cid_image_map:
                cid = src[4:].strip().lstrip('<').rstrip('>')
                self.awaitables.append(handle_cid_image(self.blocks, cid, self.cid_image_map, alt))
                self.blocks = []
            if src and src.startswith("http"):
                self.awaitables.append(add_image_block(self.blocks, src, alt))
                self.blocks = []
        if tag == "table":
            self.in_table = True
            self.table_rows = []
        if tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row_cells = []
        if tag in ("td", "th") and self.in_row:
            self.current_cell_buffer = []
            self.cell_tag_stack.append(tag)
            # src/alt already captured above
            # CID images
            if src and src.startswith("cid:") and self.cid_image_map:
                cid = src[4:].strip().lstrip('<').rstrip('>')
                self.awaitables.append(handle_cid_image(self.blocks, cid, self.cid_image_map, alt))
                self.blocks = []
            if src and src.startswith("http"):
                self.awaitables.append(add_image_block(self.blocks, src, alt))
                self.blocks = []

        elif tag == "div":
            div_classes = []  # type: List[str]
            for k, v in attrs:
                if k == "class" and isinstance(v, str):
                    div_classes.extend(v.split())
            if "toggle" in div_classes:
                self.toggle_stack.append({
                    "summary_rt": [],
                    "children": [],
                    "collecting_summary": False,
                    "phase": "root",  # root, summary, content
                })
            elif self.toggle_stack and "toggle-summary" in div_classes:
                self.toggle_stack[-1]["collecting_summary"] = True
                self.toggle_stack[-1]["phase"] = "summary"
            elif self.toggle_stack and "toggle-content" in div_classes:
                self.toggle_stack[-1]["collecting_summary"] = False
                self.toggle_stack[-1]["phase"] = "content"

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("style", "script"):
            self.skip_content = False
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            return
        if tag in ("p", "div"):
            if self.current_block == "callout":
                # Build callout block from buffered rich_text
                if self.rich_text_buffer:
                    # Strip leading whitespace from first rich_text entry's plain_text/content
                    first = self.rich_text_buffer[0]
                    if isinstance(first, dict):
                        pt = first.get("plain_text")
                        if isinstance(pt, str):
                            stripped = pt.lstrip()
                            if stripped != pt:
                                first["plain_text"] = stripped
                                if isinstance(first.get("text"), dict):
                                    first["text"]["content"] = stripped
                    # When no icon present, omit the icon field entirely (validation requirement)
                    self._append_block({
                        "object": "block",
                        "type": "callout",
                        "callout": {"rich_text": self.rich_text_buffer[:50]},
                    })
                    self.rich_text_buffer = []
                else:
                    self.flush_paragraph()
            else:
                self.flush_paragraph()
            self.current_block = None
        elif tag in ("h1", "h2", "h3"):
            # Prefer buffered rich_text segments (with per-part annotations)
            text = ""
            rich_segments: List[Dict[str, Any]] = []
            if self.rich_text_buffer:
                # Use buffered segments (already annotated)
                rich_segments = self.rich_text_buffer[:50]
                text = "".join(r.get("plain_text", "") for r in rich_segments if isinstance(r, dict)).strip()
            else:
                text = "".join(self.cur_text).strip()
                if text:
                    # Merge active annotations into single segment
                    self._append_text_segment(text[:2000])
                    rich_segments = self.rich_text_buffer[:50]
            self.cur_text = []
            if text:
                self._emit_heading(tag, rich_segments)
            self.rich_text_buffer = []
            self.current_block = None
        elif tag == "blockquote":
            # If we collected annotated rich_text segments inside the quote use them;
            # otherwise treat accumulated raw text as a single segment.
            if self.rich_text_buffer:
                self._emit_quote(self.rich_text_buffer)
                self.rich_text_buffer = []
            else:
                text = "".join(self.cur_text).strip()
                if text:
                    self._append_text_segment(text[:2000])
                    self._emit_quote(self.rich_text_buffer)
                    self.rich_text_buffer = []
            self.cur_text = []
            self.current_block = None
        elif tag in ("ul", "ol"):
            if self.list_parent_stack:
                self.list_parent_stack.pop()
            self.current_block = None
        elif tag == "li":
            # If we already buffered annotated segments (e.g., color span) emit directly
            if self.list_parent_stack:
                # Merge any raw cur_text (un-annotated) into rich_text_buffer before emit.
                raw = "".join(self.cur_text)
                # Preserve leading space + unicode dash sequences; only strip trailing newlines.
                raw = raw.rstrip("\n")
                if raw and self.rich_text_buffer:
                    # Append as a separate segment without annotations.
                    self._append_text_segment(raw[:2000])
                    self.cur_text = []
                elif raw and not self.rich_text_buffer:
                    # No rich_text yet: create single segment.
                    self._append_text_segment(raw.strip()[:2000])
                    self.cur_text = []
                if self.rich_text_buffer:
                    self._emit_list_item(self.rich_text_buffer)
                    self.rich_text_buffer = []
            # Reset current block state
            self.current_block = None
            # If we've closed a list item at outermost depth, clear parent stack
            if not self.list_parent_stack and self.list_parent_stack:
                self.list_parent_stack.clear()
        elif tag == "pre":
            text = "".join(self.cur_text)
            self.cur_text = []
            self.in_pre = False
            if text.strip():
                code_text = text.rstrip("\n")  # retain internal newlines; trim trailing
                lang = "plain text"
                if self.code_language in LANGUAGES:
                    lang = self.code_language
                self._append_block({
                    "object": "block",
                    "type": "code",
                    "code": {
                        "rich_text": _rt(code_text[:1800]),
                        "language": lang,
                        "caption": [],
                    },
                })
            self.code_language = None
            self.current_block = None
        elif tag == "a":
            # End anchor: just pop href (segments already appended with link annotations)
            if self.link_stack:
                self.link_stack.pop()
        if tag == "div" and self.toggle_stack:
            ctx = self.toggle_stack[-1]
            # Closing a nested phase div (summary/content) -> just finalize summary buffer if needed
            if ctx.get("phase") in {"summary", "content"}:
                # finalize any rich_text pending
                self.flush_paragraph()
                ctx["phase"] = "root"
            else:
                # Closing the root toggle div -> emit toggle block
                ctx = self.toggle_stack.pop()
                self.flush_paragraph()
                toggle_block = {
                    "object": "block",
                    "type": "toggle",
                    "toggle": {
                        "rich_text": ctx["summary_rt"] or _rt("Details"),
                        "children": ctx["children"][:100],
                    },
                }
                if self.toggle_stack:
                    self.toggle_stack[-1]["children"].append(toggle_block)
                else:
                    self.blocks.append(toggle_block)
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        if tag == "span" and self.skip_icon_text:
            self.skip_icon_text -= 1
        if tag in ("strong", "b", "em", "i", "u", "del", "code") and self.format_stack:
            self.format_stack.pop()
        if tag == "span" and self.format_stack:
            # Pop last color annotation
            for idx in range(len(self.format_stack) - 1, -1, -1):
                if "color" in self.format_stack[idx]:
                    self.format_stack.pop(idx)
                    break
        if tag in ("td", "th") and self.in_row and self.cell_tag_stack:
            # Finish cell
            self.cell_tag_stack.pop()
            self.current_row_cells.append(self.current_cell_buffer or [])
            self.current_cell_buffer = []
        if tag == "tr" and self.in_row:
            # Finish row
            self.in_row = False
            if self.current_row_cells:
                self.table_rows.append(self.current_row_cells)
            self.current_row_cells = []
        if tag == "table" and self.in_table:
            # Build Notion table + table_row blocks
            self.in_table = False
            # Determine headers
            has_col_header = False
            has_row_header = False
            if self.table_rows:
                # Column header: first row all header cells captured with <th> tags
                # -> we can't know now; approximate by length of first row
                # We tracked cell tag types implicitly by absence; approximate:
                # treat first row as header if any <th> used
                # Simplify: if any cell rich_text had annotations code?
                # Not reliable; we skip strict detection.
                # Provide False defaults (could enhance later).
                pass
            # Emit table block
            children = []
            # Emit row blocks
            n = max((len(r) for r in self.table_rows), default=0)
            for row in self.table_rows:
                cells_payload: List[List[Dict[str, Any]]] = []
                for cell in row:
                    # Convert rich_text buffer objects to Notion rich_text (already shaped)
                    cells_payload.append(cell[:50])
                for _ in range(len(row), n):
                    cells_payload.append([])
                children.append({
                    "object": "block",
                    "type": "table_row",
                    "table_row": {"cells": cells_payload},
                })
            if children:
                for i in range(0, len(children), 100):
                    self._append_block({
                        "object": "block",
                        "type": "table",
                        "table": {
                            "table_width": max((len(r) for r in self.table_rows), default=0),
                            "has_column_header": has_col_header,
                            "has_row_header": has_row_header,
                            "children": children[i:i + 100],
                        },
                    })
            self.table_rows = []

    def handle_data(self, data: str) -> None:
        if self.skip_content:
            return
        if self.skip_icon_text:
            return  # ignore emoji/icon text
        if self.in_pre:
            self.cur_text.append(data)
        elif self.link_stack:
            # Inside <a>: append each data segment directly as linked rich_text honoring formatting
            text = data.replace("\r", "")
            if text.strip():
                self._append_text_segment(text[:2000], link=self.link_stack[-1])
        elif self.in_row and self.cell_tag_stack:
            # Table cell content: record only to cell buffer, avoid paragraph fallback
            raw = data.replace("\r", "")
            if raw.strip():
                self._append_text_segment(raw[:2000], target=self.current_cell_buffer)
            return
        elif self.current_block == "paragraph":
            # In a paragraph or div, buffer as rich_text
            text = data.replace("\r", "")
            if text.strip():
                if self.toggle_stack and self.toggle_stack[-1].get("collecting_summary"):
                    self._append_text_segment(text[:2000], target=self.toggle_stack[-1]["summary_rt"])
                else:
                    self._append_text_segment(text[:2000])
        elif self.current_block in ("list_item", "pre"):
            # list items: if formatting (color span or other) active, store as rich_text
            text = data.replace("\r", "")
            list_has_formatting = any(
                t in ("strong", "b", "em", "i", "u", "del", "code", "span")
                for t in self.stack
            )
            if self.current_block == "list_item" and (self.format_stack or list_has_formatting):
                if text.strip():
                    self._append_text_segment(text[:2000])
            else:
                self.cur_text.append(text)
        elif self.current_block == "quote":
            text = data.replace("\r", "")
            if text.strip():
                self._append_text_segment(text[:2000])
        elif self.current_block == "heading":
            # Buffer heading segments with annotations similar to paragraph
            text = data.replace("\r", "")
            if text.strip():
                if self.toggle_stack and self.toggle_stack[-1].get("collecting_summary"):
                    self._append_text_segment(text[:2000], target=self.toggle_stack[-1]["summary_rt"])
                else:
                    self._append_text_segment(text[:2000])
        else:
            # Fallback: treat as paragraph
            text = data.replace("\r", "")
            if text.strip():
                if self.toggle_stack and self.toggle_stack[-1].get("collecting_summary"):
                    self._append_text_segment(text[:2000], target=self.toggle_stack[-1]["summary_rt"])
                else:
                    self._append_text_segment(text[:2000])


def plain_text_blocks(text: str) -> List[Dict[str, Any]]:
    """Convert plain text to paragraph block objects.

    Consecutive blank lines are treated as paragraph separators. Output is
    limited to 50 paragraphs to avoid excessively large payloads.
    """
    blocks: List[Dict[str, Any]] = []
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    for p in paragraphs[:50]:  # limit paragraphs
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rt(p[:1800])},
        })
    return blocks


async def html_to_blocks(
    html: str,
    *,
    cid_image_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Convert a subset of HTML into Notion block objects.

    Supported tags (mapped):
      p, div -> paragraph
      h1/h2/h3 -> heading_1/2/3
      ul/li -> bulleted_list_item
      ol/li -> numbered_list_item
      blockquote -> quote
      pre/code -> paragraph (code fenced) (simple)
      img -> image (external URL)

        Any <br> inside paragraphs creates a new line (split into separate paragraphs).

        Inline CID images:
            When ``cid_image_map`` is provided it should map Content-ID values (without
            angle brackets) to a dict with keys: ``data`` (bytes), ``content_type`` (str),
            and optional ``filename``. ``<img src="cid:...">`` references are converted
            into Notion external image blocks. If S3 is enabled the image bytes are
            uploaded (stable URL). Otherwise we fall back to skipping images larger
            than ~40KB to avoid excessively large data URLs; small images are inlined
            via ``data:`` URLs (best effort).

    This is intentionally *lossy* but creates a more structured representation
    than the previous plain text aggregation.
    """
    t0 = time.time()
    parser = SimpleParser(cid_image_map=cid_image_map)
    try:
        parser.feed(html)
        parser.flush_paragraph()
        final_blocks = []
        for blocks in await asyncio.gather(*parser.awaitables):
            final_blocks.extend(blocks)
        final_blocks.extend(parser.blocks)
    except Exception:
        # Fallback: treat as plain text
        return plain_text_blocks(html)
    logger.info("HTML to blocks conversion took %.2f seconds yielded %d blocks", time.time() - t0, len(final_blocks))
    # Limit total blocks to avoid huge payloads
    return final_blocks
