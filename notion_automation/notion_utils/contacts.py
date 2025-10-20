from __future__ import annotations

import logging
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses
from typing import Optional, Sequence

from notion_automation.notion_utils.html import _rt

from . import api as nua
from . import config as nuc
from . import properties as nup

logger = logging.getLogger(__name__)


async def ensure_contacts_for_emails(
    emails: Sequence[str],
    email_names: Optional[dict[str, str]] = None,
) -> list[str]:
    """Ensure a Contacts DB page exists for each provided email address.

    For each normalized email (lower-case, contains '@') this will attempt a
    query using a ``contains`` rich_text filter on the Contacts Email property.
    Missing contacts are created with a title equal to the local-part (before
    ``@``) and the Email property set to the full address. Creation failures
    are logged and skipped.

    Returns list of contact page IDs (existing + newly created) preserving the
    input order for first occurrences. Duplicate emails are de-duplicated.

    This helper is best-effort: if the Contacts DB ID or headers are missing it
    returns an empty list. Callers should treat an empty return as 'no contacts'.
    """
    # Normalize and de-duplicate input emails
    norm: list[str] = []
    seen: set[str] = set()
    for e in emails:
        if not isinstance(e, str):
            continue
        el = e.strip().lower()
        if '@' not in el:
            continue
        if el not in seen:
            seen.add(el)
            norm.append(el)
    if not norm:
        return []
    contact_ids: list[str] = []
    # Query existing contacts in batches (limit OR filter size ~25)
    batch_size = 25
    email_to_contact: dict[str, str] = {}
    contacts_db_id = nuc.NOTION_CONTACTS_DB_ID
    email_prop_type = (await nua.get_database_property_type(contacts_db_id, nuc.PROP_CONTACTS_EMAIL)) or "rich_text"
    for i in range(0, len(norm), batch_size):
        chunk = norm[i:i+batch_size]
        or_filters = []
        for addr in chunk:
            if email_prop_type == "email":
                or_filters.append({"property": nuc.PROP_CONTACTS_EMAIL, "email": {"equals": addr[:200]}})
            else:
                or_filters.append({"property": nuc.PROP_CONTACTS_EMAIL, "rich_text": {"contains": addr[:200]}})
        payload = {"filter": {"or": or_filters}, "page_size": 100}
        try:
            data = await nua.query_database(contacts_db_id, payload)
            results = data.get("results", []) if isinstance(data, dict) else []
            for page in results:
                if not isinstance(page, dict):
                    continue
                props = page.get("properties", {}) if isinstance(page.get("properties"), dict) else {}
                email_prop = props.get(nuc.PROP_CONTACTS_EMAIL)
                found_emails = nup.extract_emails(email_prop)
                pid = page.get("id") if isinstance(page.get("id"), str) else None
                if pid:
                    for fe in found_emails:
                        if fe not in email_to_contact:
                            email_to_contact[fe] = pid
        except Exception:  # pragma: no cover
            logger.debug("ensure_contacts_for_emails query failure", exc_info=True)
    # Create missing contacts
    title_prop = (await nua.get_database_title_property(contacts_db_id)) or "Name"
    for e in norm:
        if e in email_to_contact:
            continue
        # Prefer provided display name; fallback to local-part
        display = e.split('@')[0]
        display = ' '.join([s.capitalize() for x in display.split('.') if (s := x.strip())])
        if email_names:
            nm = email_names.get(e)
            if isinstance(nm, str) and nm.strip():
                display = nm.strip()
        display = display[:50]
        if email_prop_type == "email":
            props = {
                title_prop: {"title": _rt(display)},
                nuc.PROP_CONTACTS_EMAIL: {"email": e},
            }
        elif email_prop_type == "title":
            # Unusual but support if Email property is actually title type
            props = {
                title_prop: {"title": _rt(display)},
                nuc.PROP_CONTACTS_EMAIL: {"title": _rt(e)},
            }
        else:  # default rich_text
            props = {
                title_prop: {"title": _rt(display)},
                nuc.PROP_CONTACTS_EMAIL: {"rich_text": _rt(e)},
            }
        try:
            pid = await nua.create_page(contacts_db_id, props)
            if pid:
                email_to_contact[e] = pid
                logger.info("Created new contact %s -> %s", e, pid)
        except Exception:  # pragma: no cover
            logger.debug("Failed creating contact %s", e, exc_info=True)
    # Preserve order of first occurrence
    for e in norm:
        pid = email_to_contact.get(e)
        if pid and pid not in contact_ids:
            contact_ids.append(pid)
    return contact_ids


async def find_partners_for_emails(emails: Sequence[str]) -> list[str]:
    """Return partner relation page IDs for any matching contact emails.

    Performs a single OR query using rich_text ``contains`` filters for each
    provided email against the Contacts database Email property. All matching
    contacts' Partner relation IDs are aggregated and de-duplicated.

    This broadens the previous single-email equals lookup so that any address
    present in the From / To / Cc headers (external to the company domain) can
    yield an associated Partner relation. Using ``contains`` instead of
    ``equals`` supports cases where the Contacts DB Email property stores
    multiple comma-separated addresses or display names.

    Args:
        contacts_db_id: Contacts database ID.
        emails: Iterable of email strings (will be normalized & de-duped).

    Returns:
        list of partner page IDs if no matches found / on error.
    """
    contacts_db_id = nuc.NOTION_CONTACTS_DB_ID
    if not contacts_db_id:
        return []
    # Normalize & de-dupe; ignore obviously invalid entries lacking '@'
    unique: list[str] = []
    seen: set[str] = set()
    for e in emails:
        if not isinstance(e, str):
            continue
        e_low = e.strip().lower()
        if '@' not in e_low:
            continue
        if e_low not in seen:
            seen.add(e_low)
            unique.append(e_low)
    if not unique:
        return []
    # Limit to a reasonable number to avoid overly large OR filters
    limited = unique[:25]
    email_prop_type = (await nua.get_database_property_type(contacts_db_id, nuc.PROP_CONTACTS_EMAIL)) or "rich_text"
    or_filters = []
    for addr in limited:
        if email_prop_type == "email":
            # Notion email property only supports equals/does_not_equal; use equals
            or_filters.append({"property": nuc.PROP_CONTACTS_EMAIL, "email": {"equals": addr[:200]}})
        else:
            or_filters.append({"property": nuc.PROP_CONTACTS_EMAIL, "rich_text": {"contains": addr[:200]}})
    payload = {"filter": {"or": or_filters}, "page_size": 100}
    try:
        data = await nua.query_database(contacts_db_id, payload)
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return []
        partner_ids: list[str] = []
        seen_partner: set[str] = set()
        for page in results:
            if not isinstance(page, dict):
                continue
            props = page.get("properties", {}) if isinstance(page.get("properties"), dict) else {}
            partner_prop = props.get(nuc.PROP_CONTACTS_PARTNER_REL)
            if isinstance(partner_prop, dict):
                rel = partner_prop.get("relation")
                if isinstance(rel, list):
                    for r in rel:
                        if isinstance(r, dict):
                            rid = r.get("id")
                            if isinstance(rid, str) and rid not in seen_partner:
                                seen_partner.add(rid)
                                partner_ids.append(rid)
        return partner_ids
    except Exception as e:  # pragma: no cover
        logger.debug("find_partners_for_emails failure: %s", e)
        return []


async def get_contacts(msg: Message, emails: Sequence[str]) -> list[str]:
    try:
        domain = nuc.ENGINEERING_ALIAS.split('@')[-1]
        # Build mapping of email -> display name from headers (From/To/Cc)
        name_email_pairs = getaddresses(msg.get_all('From', []) + msg.get_all('To', []) + msg.get_all('Cc', []))
        email_names: dict[str, str] = {}
        for display, addr in name_email_pairs:
            if not addr or '@' not in addr:
                continue
            addr_l = addr.lower()
            if addr_l.split('@')[-1] == domain:
                continue  # skip internal
            if not display:
                continue
            # Decode any RFC2047 encoded words in display name
            try:
                dh = decode_header(display)
                disp_decoded = str(make_header(dh)).strip()
            except Exception:
                disp_decoded = display.strip()
            if disp_decoded and addr_l not in email_names:
                email_names[addr_l] = disp_decoded[:50]
        # Candidate set: external unique emails
        candidate_emails: list[str] = []
        for addr in emails:
            if addr.split('@')[-1] != domain and addr not in candidate_emails:
                candidate_emails.append(addr)
        if candidate_emails:
            return await ensure_contacts_for_emails(candidate_emails, email_names)
    except Exception:  # pragma: no cover
        logger.debug("Contact ensure failure", exc_info=True)
    return []
