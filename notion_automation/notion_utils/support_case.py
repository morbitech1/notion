from __future__ import annotations

import logging
import re
import time
from email.message import Message
from typing import Any, Optional, Sequence

from notion_automation.notion_utils.contacts import find_partners_for_emails
from notion_automation.types import JSON

from .. import email_utils as eu
from . import api as nua
from . import config as nuc
from . import html as nuh

logger = logging.getLogger(__name__)


async def build_support_case_properties(
    subject: str,
    ticket_id: Optional[str],
    case_type: str,
    *,
    title_prop: str,
    status: str = nuc.VAL_STATUS_OPEN,
    partner_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Construct property map for a new support case page.

    Performs light normalization of ``case_type`` to one of the configured
    select option names when it starts with a recognized prefix.

    Args:
        subject: Email subject.
        ticket_id: Optional ticket identifier (random timestamp fragment if missing).
        case_type: Type label (technical vs support).

    Returns:
        Properties dict suitable for Notion page creation.
    """
    # Map external case_type to configured select value names. Expect caller to pass either
    # something matching VAL_TYPE_* or a free-form value; minimal normalization here.
    db_id = nuc.NOTION_SUPPORT_CASES_DB_ID
    type_value = case_type
    if case_type.lower().startswith("tech"):
        type_value = nuc.VAL_TYPE_TECHNICAL
    elif case_type.lower().startswith("supp"):
        type_value = nuc.VAL_TYPE_SUPPORT
    title_key = title_prop
    props: dict[str, Any] = {
        title_key: {"title": nuh._rt(subject[:200])},
        nuc.PROP_SUPPORT_CASE_STATUS: {
            await nua.get_database_property_type(db_id, nuc.PROP_SUPPORT_CASE_STATUS): {"name": status}},
        nuc.PROP_SUPPORT_CASE_TYPE: {
            await nua.get_database_property_type(db_id, nuc.PROP_SUPPORT_CASE_TYPE): [{"name": type_value}]},
    }
    if not ticket_id:
        ticket_id = str(time.time())[:10]
    props[nuc.PROP_SUPPORT_CASE_TICKET_ID] = {"rich_text": nuh._rt(ticket_id)}
    if partner_ids:
        props[nuc.PROP_SUPPORT_CASE_PARTNER_REL] = {"relation": [{"id": pid} for pid in partner_ids[:10]]}
    return props


async def find_support_case(
    ticket_id: Optional[str],
    subject: str,
    title_prop: str,
    emails_db_id: Optional[str] = None,
    reference_msg_ids: Optional[Sequence[str]] = None,
) -> Optional[JSON]:
    """Attempt to locate a support case page using Notion queries.

    Strategy:
      1. If a ``ticket_id`` is present, query rich_text equals filter on Ticket ID property.
      2. Fallback: normalize subject (strip Re/Fwd prefixes) and attempt an OR query
         matching either exact subject or variant without prefix using the title property.

    Returns the full page object if found else ``None``.
    """
    def norm(s: str) -> str:
        return re.sub(r"^(re|fw|fwd)[:\]]\s*", "", s, flags=re.I).strip()

    try:
        if ticket_id:
            payload = {
                "filter": {
                    "property": nuc.PROP_SUPPORT_CASE_TICKET_ID,
                    "rich_text": {"contains": ticket_id},  # contains handles embedded IDs
                },
                "page_size": 1,
            }
            res = await nua.query_database(nuc.NOTION_SUPPORT_CASES_DB_ID, payload)
            results = res.get("results", []) if isinstance(res, dict) else []
            if results:
                return results[0]
        subject_norm = subject.strip()
        variants = list({subject_norm, norm(subject_norm)})
        # Build OR filter over title rich_text equals each variant
        or_filters = []
        for v in variants:
            if not v:
                continue
            or_filters.append({
                "property": title_prop,
                "title": {"equals": v[:100]},  # Notion title equals filter
            })
        if not or_filters:
            return None
        # Fetch up to a few candidate pages (duplicate titles possible)
        payload2 = {"filter": {"or": or_filters}, "page_size": 5}
        res2 = await nua.query_database(nuc.NOTION_SUPPORT_CASES_DB_ID, payload2)
        results2 = res2.get("results", []) if isinstance(res2, dict) else []
        if not results2:
            return None
        # If no reference message IDs or no emails DB provided, fall back to title-only matching
        ref_ids = [r.strip() for r in (reference_msg_ids or []) if isinstance(r, str) and r.strip()]
        if not ref_ids or not emails_db_id:
            return results2[0]
        # Require at least one overlap: any email linked to the case having a Message ID contained
        # in the incoming email's References / In-Reply-To derived list
        for page in results2:
            page_id = page.get("id") if isinstance(page, dict) and isinstance(page.get("id"), str) else None
            if not page_id:
                continue
            # Build OR filters for message id contains for each reference id
            mid_filters = []
            for mid in ref_ids[:25]:  # limit to keep filter size reasonable
                mid_filters.append({
                    "property": nuc.PROP_EMAILS_MESSAGE_ID,
                    "rich_text": {"contains": mid[:200]},
                })
            if not mid_filters:
                continue
            relation_filter = {
                "property": nuc.PROP_EMAILS_SUPPORT_CASE_REL,
                "relation": {"contains": page_id},
            }
            payload_emails = {
                "filter": {
                    "and": [relation_filter, {"or": mid_filters}],
                },
                "page_size": 1,
            }
            try:
                email_query = await nua.query_database(emails_db_id, payload_emails)
            except Exception:  # pragma: no cover - defensive
                continue
            email_results = email_query.get("results", []) if isinstance(email_query, dict) else []
            if email_results:
                return page  # Overlap confirmed
        # No candidate satisfied overlap requirement
        return None
    except Exception as e:  # pragma: no cover
        logger.debug("find_support_case query failure: %s", e)
    return None


def extract_ticket_id(msg: Message) -> Optional[str]:
    """Extract 10-digit ticket ID in square brackets from subject or body.

    Args:
        msg: Email message.

    Returns:
        The 10-digit ticket string if found, else ``None``.
    """
    # Search subject then body text for pattern [##########]
    subject = eu.get_decoded_subject(msg)
    m = nuc.TICKET_REGEX.search(subject)
    if m:
        return m.group(1)
    # Walk parts for text
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ("text/plain", "text/html"):
                    payload_bytes = part.get_payload(decode=True)
                    if not isinstance(payload_bytes, (bytes, bytearray)):
                        continue
                    try:
                        text = payload_bytes.decode(part.get_content_charset() or 'utf-8', errors='replace')
                    except Exception:
                        continue
                    m2 = nuc.TICKET_REGEX.search(text)
                    if m2:
                        return m2.group(1)
        else:
            payload_bytes = msg.get_payload(decode=True)
            if isinstance(payload_bytes, (bytes, bytearray)):
                try:
                    text = payload_bytes.decode(msg.get_content_charset() or 'utf-8', errors='replace')
                    m3 = nuc.TICKET_REGEX.search(text)
                    if m3:
                        return m3.group(1)
                except Exception:
                    return None
    except Exception:  # pragma: no cover simple
        return None
    return None


async def find_or_create_support_case(email_msg: Message) -> Optional[str]:
    """Async lookup/create support case using newly async notion_utils helpers.

    Returns page ID or None.
    """
    db_id = nuc.NOTION_SUPPORT_CASES_DB_ID
    from_addrs, to_addrs, cc_addrs = eu.get_message_addresses(email_msg)
    bcc_addrs = eu.extract_bcc_addresses(email_msg)
    all_addrs = set(to_addrs + cc_addrs + bcc_addrs)
    domain = nuc.ENGINEERING_ALIAS.split('@')[-1]
    tracking_email = nuc.TRACKING_ALIAS in all_addrs
    if tracking_email:
        case_type = "Tracking"
    elif nuc.ENGINEERING_ALIAS in all_addrs:
        case_type = "Technical"
    elif nuc.SUPPORT_ALIAS in all_addrs:
        case_type = "Support"
    else:
        return None
    subject = eu.get_decoded_subject(email_msg)
    if eu.is_draft(email_msg):
        logger.debug("Skipping draft email subject=%r", subject)
        return None

    ticket_id = extract_ticket_id(email_msg)
    title_prop = await nua.get_database_title_property(db_id)
    assert title_prop is not None, "Support Cases DB must have a title property"
    contacts_db = nuc.NOTION_CONTACTS_DB_ID
    partner_ids: list[str] = []
    if contacts_db:
        candidate_addrs: list[str] = []
        for addr in from_addrs + to_addrs + cc_addrs:
            if addr.split('@')[-1] != domain:
                candidate_addrs.append(addr)
        if candidate_addrs:
            try:
                partner_ids = await find_partners_for_emails(candidate_addrs)
            except Exception:  # pragma: no cover
                logger.debug("Partner lookup failure", exc_info=True)
    # Collect reference message IDs (References + In-Reply-To + Message-ID of incoming) for stricter title matching
    references_hdrs = email_msg.get_all('References', []) or []
    # Split on whitespace to individual tokens (Message-IDs often separated by spaces)
    reference_tokens: list[str] = []
    for r in references_hdrs:
        if not isinstance(r, str):
            continue
        # Message-ID tokens are typically bracketed; keep raw so 'contains' works
        parts = r.strip().split()
        for p in parts:
            if p not in reference_tokens:
                reference_tokens.append(p)
    in_reply_to = email_msg.get('In-Reply-To')
    if isinstance(in_reply_to, str) and in_reply_to.strip() and in_reply_to.strip() not in reference_tokens:
        reference_tokens.append(in_reply_to.strip())
    # Include the current Message-ID so future replies can match
    cur_msg_id = email_msg.get('Message-ID') or email_msg.get('Message-Id')
    if isinstance(cur_msg_id, str) and cur_msg_id.strip() and cur_msg_id.strip() not in reference_tokens:
        reference_tokens.append(cur_msg_id.strip())
    emails_db_id = nuc.NOTION_EMAILS_DB_ID
    existing_page = await find_support_case(ticket_id, subject, title_prop, emails_db_id, reference_tokens)
    if existing_page:
        existing = existing_page.get("id") if isinstance(existing_page.get("id"), str) else None
        props = existing_page.get("properties", {}) if isinstance(existing_page, dict) else {}
        outside_domain = not any(e.split('@')[-1] == domain for e in from_addrs)
        status_prop = props.get(nuc.PROP_SUPPORT_CASE_STATUS, {})
        status_type = status_prop.get('type')
        current_status = None
        inner = status_prop.get(status_type)
        if isinstance(inner, dict):
            nm = inner.get("name")
            if isinstance(nm, str):
                current_status = nm
        needs_status = outside_domain and current_status not in (nuc.VAL_STATUS_OPEN, nuc.VAL_STATUS_NEW_REPLY)
        partner_already = False
        if partner_ids:
            partner_prop = props.get(nuc.PROP_SUPPORT_CASE_PARTNER_REL)
            if isinstance(partner_prop, dict):
                rel = partner_prop.get("relation")
                if isinstance(rel, list) and rel:
                    partner_already = True
        patch_properties: dict[str, object] = {}
        if needs_status:
            patch_properties[nuc.PROP_SUPPORT_CASE_STATUS] = {status_type: {"name": nuc.VAL_STATUS_NEW_REPLY}}
        if partner_ids and not partner_already:
            patch_properties[nuc.PROP_SUPPORT_CASE_PARTNER_REL] = {
                "relation": [{"id": pid} for pid in partner_ids[:10]]}
        if patch_properties and existing:
            await nua.patch_page(existing, patch_properties)
        return existing
    props_create = await build_support_case_properties(
        subject,
        ticket_id,
        case_type,
        title_prop=title_prop,
        status=nuc.VAL_STATUS_OPEN if not tracking_email else nuc.VAL_STATUS_RESOLVED,
        partner_ids=partner_ids,
    )
    page_id = await nua.create_page(nuc.NOTION_SUPPORT_CASES_DB_ID, props_create, children=None)
    if page_id:
        logger.info("Created support case page=%s subject=%r ticket_id=%s type=%s partner=%s",
                    page_id, subject, ticket_id, case_type, bool(partner_ids))
    return page_id
