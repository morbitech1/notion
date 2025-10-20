from __future__ import annotations

import logging
from email.message import Message
from typing import Optional
from urllib.parse import quote

from notion_automation.notion_utils.contacts import get_contacts

from .. import email_utils as eu
from .. import s3_utils as su
from . import api as nua
from . import config as nuc
from . import html as nuh

logger = logging.getLogger(__name__)


async def build_email_content_blocks(msg: Message) -> list[dict]:
    """Extract message body parts and convert to Notion blocks.

    Preference order:
      1. First text/html part (converted with html_to_blocks)
      2. Fallback to aggregated text/plain parts (plain_text_blocks)

    Returns list of block objects (may be empty). Images inside HTML become
    image blocks referencing external URLs (Gmail usually proxy URLs already).
    """
    html_part: Optional[str] = None
    text_parts: list[str] = []
    # Map of Content-ID (without <>) -> {data: bytes, content_type: str, filename: str}
    cid_image_map: dict[str, dict[str, object]] = {}
    if msg.is_multipart():
        for part in msg.walk():
            disp = part.get_content_disposition()
            if disp == 'attachment':
                continue
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                payload_bytes = part.get_payload(decode=True)
                if not isinstance(payload_bytes, (bytes, bytearray)):
                    continue
                try:
                    text = payload_bytes.decode(part.get_content_charset() or 'utf-8', errors='replace')
                except Exception:
                    continue
                if ct == 'text/html' and html_part is None and text.strip():
                    html_part = text
                elif ct == 'text/plain' and text.strip():
                    text_parts.append(text)
            # Inline related image (part of multipart/related) often has Content-ID
            elif ct.startswith("image/"):
                # Treat inline images (no attachment disposition or explicit inline)
                cid_raw = part.get('Content-ID') or part.get('Content-Id')
                if cid_raw:
                    cid_clean = cid_raw.strip().lstrip('<').rstrip('>')
                    if cid_clean and cid_clean not in cid_image_map:
                        payload_bytes = part.get_payload(decode=True)
                        if isinstance(payload_bytes, (bytes, bytearray)) and payload_bytes:
                            filename = part.get_filename() or f"inline-{cid_clean}"
                            cid_image_map[cid_clean] = {
                                "data": payload_bytes,
                                "content_type": ct,
                                "filename": filename,
                            }
    else:
        payload_bytes = msg.get_payload(decode=True)
        if isinstance(payload_bytes, (bytes, bytearray)):
            try:
                only_text = payload_bytes.decode(msg.get_content_charset() or 'utf-8', errors='replace')
                if msg.get_content_type() == 'text/html':
                    html_part = only_text
                else:
                    text_parts.append(only_text)
            except Exception:
                pass
    if html_part:
        blocks = await nuh.html_to_blocks(html_part, cid_image_map=cid_image_map if cid_image_map else None)
        # Detect quoted previous thread / reply history and collapse into a toggle block.
        # Heuristics:
        #   - Gmail quotes often inside <div class="gmail_quote"> ...
        #   - Lines starting with 'On <date> <name> wrote:' followed by blockquote/divs.
        # Strategy: after initial conversion, scan for a blockquote or paragraph sequence
        # containing 'On ' and 'wrote:' or any paragraph with 'gmail_quote' marker (lost in parsing).
        try:
            # Build plain text view of each block to search markers
            def block_plain(b: dict) -> str:
                t = b.get("type")
                if not t:
                    return ""
                sec = b.get(t, {}) if isinstance(b.get(t), dict) else {}
                rt = sec.get("rich_text") if isinstance(sec, dict) else None
                txt = ""
                if isinstance(rt, list):
                    for r in rt:
                        if isinstance(r, dict):
                            pt = r.get("plain_text")
                            if isinstance(pt, str):
                                txt += pt + " "
                return txt.strip()
            marker_indices: list[int] = []
            for i, b in enumerate(blocks):
                txt = block_plain(b).lower()
                if not txt:
                    continue
                if txt.startswith("on ") and " wrote:" in txt:
                    marker_indices.append(i)
            # If we found a marker, group trailing blocks (up to limit) as previous thread
            if marker_indices:
                start = marker_indices[0]
                tail_blocks = blocks[start:]
                if tail_blocks:
                    toggle_block = {
                        "object": "block",
                        "type": "toggle",
                        "toggle": {
                            "rich_text": nuh._rt("Previous thread"),
                            "children": tail_blocks[:50],
                        },
                    }
                    blocks = blocks[:start] + [toggle_block]
        except Exception:  # pragma: no cover
            pass
    else:
        combined = "\n\n".join(text_parts)
        blocks = nuh.plain_text_blocks(combined)
    return blocks


async def create_email_record(msg: Message, support_case_id: Optional[str], uid: Optional[int]) -> str | None:
    """Async create an Email log page in Notion for the message."""
    emails_db = nuc.NOTION_EMAILS_DB_ID
    if not emails_db:
        return None
    subject = eu.get_decoded_subject(msg)
    from_addrs, to_addrs, cc_addrs = eu.get_message_addresses(msg)
    # build_email_content_blocks is sync (parsing only)
    blocks = await build_email_content_blocks(msg)
    # Collect or create related contacts (external addresses only) if contacts DB configured
    contact_rel_ids: list[str] = await get_contacts(msg, from_addrs + to_addrs + cc_addrs)
    # Collect attachment info (filenames or S3 URLs if upload enabled)
    filenames: list[str] = []  # Gmail anchor fallback list (used when S3 disabled)
    s3_files: list[dict] = []  # Notion file objects when uploading
    uploading = su.s3_enabled()
    if uploading:
        logger.debug("S3 attachments enabled; attempting upload of parts")
    for part in msg.walk():
        if part.get_content_disposition() == 'attachment':
            name = part.get_filename() or 'attachment'
            if uploading:
                try:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, (bytes, bytearray)):
                        ctype = part.get_content_type()
                        url = await su.s3_upload(name, payload, ctype)
                        if url:
                            s3_files.append({
                                "name": name[:100],
                                "type": "external",
                                "external": {"url": url},
                            })
                            continue  # Skip adding to filenames fallback
                except Exception:  # pragma: no cover - best effort
                    logger.debug("Failed S3 upload for %s", name, exc_info=True)
            if isinstance(name, str):
                filenames.append(name)
    # Build properties using correct types (multi_select for To/CC, email for From)
    title_prop = (await nua.get_database_title_property(emails_db)) or nuc.PROP_EMAILS_NAME
    props: dict = {
        title_prop: {"title": nuh._rt(subject[:200])},
        nuc.PROP_EMAILS_TO: {"multi_select": [{"name": a} for a in to_addrs[:50]]},
        nuc.PROP_EMAILS_FROM: {"email": (from_addrs[0] if from_addrs else "")},
    }
    if cc_addrs:
        props[nuc.PROP_EMAILS_CC] = {"multi_select": [{"name": a} for a in cc_addrs[:50]]}
    if support_case_id:
        props[nuc.PROP_EMAILS_SUPPORT_CASE_REL] = {"relation": [{"id": support_case_id}]}
    if contact_rel_ids:
        # Attach contacts relation pages (limit 100 to stay safe)
        props[nuc.PROP_EMAILS_CONTACTS_REL] = {"relation": [{"id": cid} for cid in contact_rel_ids[:100]]}
    files_list = []
    # Gmail specific headers (when using Gmail IMAP):
    # X-GM-THRID (thread id, numeric), X-GM-MSGID (message id, numeric)
    email_link = None
    if gm_thrid := msg.get('X-GM-THRID'):
        email_link = f"https://mail.google.com/mail/u/0/#inbox/%23thread-f%3A{gm_thrid}"
        props[nuc.PROP_EMAILS_THREAD_ID] = {"rich_text": nuh._rt(gm_thrid)}
        files_list.append({
            "name": "Email (thread)",
            "type": "external",
            "external": {"url": email_link},
        })
    # Capture raw Message-ID / References headers (rich_text so they are searchable)
    msg_id = msg.get('Message-ID') or msg.get('Message-Id')
    if isinstance(msg_id, str) and msg_id.strip():
        msg_id = msg_id.strip()[:2000]
        props[nuc.PROP_EMAILS_MESSAGE_ID] = {"rich_text": nuh._rt(msg_id)}
        files_list.append({
            "name": "Email (message)",
            "type": "external",
            "external": {"url": f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{msg_id}"},
        })
    refs = msg.get_all('References', [])
    if refs:
        refs_joined = " ".join(r for r in refs if isinstance(r, str))[:2000]
        if refs_joined:
            props[nuc.PROP_EMAILS_REFERENCES] = {"rich_text": nuh._rt(refs_joined)}
    if files_list:
        props[nuc.PROP_EMAILS_LINK] = {"files": files_list[:10]}
    # Prefer S3 uploaded file URLs if available; else Gmail anchors fallback
    if s3_files:
        props[nuc.PROP_EMAILS_ATTACHMENTS] = {"files": s3_files[:100]}
    elif filenames and email_link:
        attachment_files = []
        for fn in filenames[:100]:
            safe = quote(fn, safe="")
            attachment_files.append({
                "name": fn[:100],
                "type": "external",
                "external": {"url": f"{email_link}#att-{safe}"},
            })
        if attachment_files:
            props[nuc.PROP_EMAILS_ATTACHMENTS] = {"files": attachment_files}
    # If UID provided, attempt dedupe by querying existing record
    if uid is not None:
        try:
            filter_payload = {
                "filter": {
                    "property": nuc.PROP_EMAILS_UID,
                    "rich_text": {"equals": str(uid)},
                },
                "page_size": 1,
            }
            existing = (await nua.query_database(emails_db, filter_payload)).get("results", [])
            if existing:
                logger.debug("Email UID %s already recorded; skipping creation", uid)
                return existing[0].get("id")
            props[nuc.PROP_EMAILS_UID] = {"rich_text": nuh._rt(str(uid))}
        except Exception:
            # Proceed; property may not exist yet
            pass
    page_id = await nua.create_page(emails_db, props, children=blocks[:90])
    if page_id:
        logger.info("Created email record page=%s subject=%r support_case=%s to=%s",
                    page_id, subject, support_case_id, to_addrs)
    else:
        logger.warning("Failed to create email record subject=%r", subject)
    return page_id
