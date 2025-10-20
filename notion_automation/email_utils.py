from __future__ import annotations

import html as html_lib
import logging
import mimetypes
import os
import re
import smtplib
from collections import defaultdict
from email.header import decode_header, make_header
from email.message import Message
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
GMAIL_USER = os.environ.get("GMAIL_USER", '')
GMAIL_PASS = os.environ.get("GMAIL_PASS", '')
IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")
BRAND_NAME = os.getenv("BRAND_NAME", "Our Team")
BRAND_ICON_URL = os.getenv("BRAND_ICON_URL")
EMAIL_FOOTER_TEXT = os.getenv("EMAIL_FOOTER_TEXT", f"You received this email from {BRAND_NAME}.")
GMAIL_USER = os.getenv("GMAIL_USER", '')
GMAIL_PASS = os.getenv("GMAIL_PASS", '')
SUBJECT_SPLIT = re.compile(r'(\b[Ff][Ww][Dd]?:|\bR[eE]:|\bREPLY-\d+:)')
# Magic regex: https://stackoverflow.com/a/201378
EMAIL_PAT = re.compile(
    r'''(?:[a-z0-9!#$%&'*+\x2f=?^_`\x7b-\x7d~\x2d]+(?:\.[a-z0-9!#$%&'*'''
    r'''+\x2f=?^_`\x7b-\x7d~\x2d]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21'''
    r'''\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9]'''
    r'''(?:[a-z0-9\x2d]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9\x2d]*[a-z0-9])?|'''
    r'''\[(?:(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9]))\.)'''
    r'''{3}(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9])|'''
    r'''[a-z0-9\x2d]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f'''
    r'''\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])''',
    flags=re.IGNORECASE,
)
_FWD_MARKERS = [
    "---------- forwarded message ----------",
    "-----original message-----",
    "forwarded message",
    "original message"
]


def find_emails(s: str):
    """Return list of email addresses found in string s."""
    res = []
    while pat := EMAIL_PAT.search(s):
        res.append(pat.group())
        s = s[pat.end():]
    return res


def clean_subject(subject: str) -> str:
    """Normalize an email / page subject for matching & storage.

    Operations:
      * Strip common reply / forward prefixes (``Re:``, ``Fwd:``, ``REPLY-<n>:``)
      * Remove leading/trailing colons & whitespace
      * Collapse newlines / carriage returns into single spaces
      * Truncate to 200 characters (safety for Notion title filters)

    Args:
        subject: Raw subject string (may contain quoting artifacts).

    Returns:
        A cleaned, length‑bounded subject suitable for equality comparisons.
    """
    subject = SUBJECT_SPLIT.split(subject)[-1].strip(':').strip()
    subject = re.sub(r'[\r\n]+', ' ', subject)
    return subject[:200]


def get_decoded_subject(msg: Message) -> str:
    """Return decoded Subject header (RFC 2047) or fallback.

    Collapses folding whitespace and normalizes via clean_subject helper.
    """
    raw = msg.get('Subject')
    if not raw:
        return '(No Subject)'
    try:
        dh = decode_header(raw)
        subject = str(make_header(dh))
    except Exception:
        subject = raw
    return clean_subject(subject) or '(No Subject)'


def _decode_text_bodies(msg: Message) -> List[str]:
    """Return list of decoded textual bodies (preferring text/plain parts).

    For forwarded header detection we only need a best-effort plain text view.
    If only HTML is available we strip tags naively.
    """
    texts: List[str] = []
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() != 'text':
                    continue
                ct = part.get_content_type()
                if ct not in ("text/plain", "text/html"):
                    continue
                payload = part.get_payload(decode=True)
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                try:
                    txt = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                except Exception:
                    continue
                if ct == 'text/html':
                    # crude tag strip
                    txt = re.sub(r'<[^>]+>', ' ', txt)
                texts.append(txt)
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, (bytes, bytearray)):
                try:
                    txt = payload.decode(msg.get_content_charset() or 'utf-8', errors='replace')
                except Exception:
                    txt = ''
                if msg.get_content_type() == 'text/html':
                    txt = re.sub(r'<[^>]+>', ' ', txt)
                texts.append(txt)
    except Exception:  # pragma: no cover - defensive
        return texts
    return texts


def extract_forwarded_original_headers(msg: Message) -> Optional[dict[str, List[str]]]:
    """Best-effort extraction of original From/To/Cc headers from a forwarded email.

    Detects common forwarded header blocks inserted by MUAs (Gmail, Outlook, Apple Mail):
      - Lines containing 'Forwarded message' or 'Original Message'
      - A contiguous block starting with 'From:' followed by at least one of 'To:' or 'Subject:' within a few lines.

    Returns a dict with keys 'from', 'to', 'cc' mapping to lists of addresses (lower-cased).
    Only returns a value when at least two header fields are confidently detected to reduce false positives.
    """
    bodies = _decode_text_bodies(msg)
    if not bodies:
        return None
    for body in bodies:
        if not body:
            continue
        # Normalize newlines and strip excessive spaces
        norm_lines = [line.strip() for line in re.split(r'[\r\n]+', body)]
        # Remove leading quoting symbols '>' for detection (keep original order)
        norm_lines = [re.sub(r'^[>\*]+\s*', '', line).strip() for line in norm_lines]
        norm_lines = [line for line in norm_lines if line]
        # Quick marker scan
        candidate_indices: List[int] = []
        email_participant_re = re.compile(r'^(From|To|Cc|CC)\s*:\s*(.+)$', flags=re.I)
        for i, line in enumerate(norm_lines[:3]):
            low = line.lower()
            if any(m in low for m in _FWD_MARKERS):
                candidate_indices.append(i + 1)  # start likely after marker line
            if email_participant_re.match(line):
                candidate_indices.append(i)  # start at this line
        for start in candidate_indices:
            key = None
            headers_found: dict[str, list[str]] = defaultdict(list)
            for j in range(start, min(start + 20, len(norm_lines))):
                line = norm_lines[j]
                if not line:
                    break  # end of header block
                m = email_participant_re.match(line)
                if m:
                    key = m.group(1).capitalize()
                if key and (emails := find_emails(line)):
                    headers_found[key.lower()].extend({e.lower() for e in emails})
                elif ':' not in line:
                    break
            if headers_found:
                return {
                    'from': headers_found.get('from', []),
                    'to': headers_found.get('to', []),
                    'cc': headers_found.get('cc', []),
                }
    return None


def extract_addresses(msg: Message, header_name: str) -> List[str]:
    """Return lower-cased email addresses from a header (To/Cc/From).

    Args:
        msg: Email message.
        header_name: Header field name (``To``, ``Cc``, ``From`` etc.).

    Returns:
        List of extracted email addresses (may be empty).
    """
    vals = msg.get_all(header_name, [])
    addrs = [cleaned for a in vals for cleaned in find_emails(a)]
    return [a.lower() for a in addrs]


def get_message_addresses(msg: Message) -> tuple[List[str], List[str], List[str]]:
    """Return (from_addrs, to_addrs, cc_addrs) considering forwarded emails.

    If a forwarded block is detected we prefer the original headers; otherwise
    we return the message envelope headers. Reply-To is honored for the base message.
    """
    base_from = extract_addresses(msg, 'Reply-To') or extract_addresses(msg, 'From')
    base_to = extract_addresses(msg, 'To')
    base_cc = extract_addresses(msg, 'Cc')
    forwarded = extract_forwarded_original_headers(msg)
    if forwarded:
        f_from = forwarded.get('from') or base_from
        f_to = forwarded.get('to') or [] or base_to
        f_cc = forwarded.get('cc') or []
        all_emails = {*base_from, *base_to, *base_cc, *f_from, *f_to, *f_cc}
        missing = list(all_emails - {*f_from, *f_to})
        return (f_from, f_to, missing)
    return (base_from, base_to, base_cc)


def is_draft(msg: Message) -> bool:
    """Return True if message appears to be an unsent draft (heuristic).

    Args:
        msg: Email message.

    Returns:
        True if draft indicators present, else False.
    """
    return bool(msg.get('X-Gmail-Draft'))


def extract_bcc_addresses(msg: Message) -> List[str]:
    """Return list of probable Bcc / envelope recipients.

    Because Bcc headers are typically stripped from the delivered copy, we
    also look at common MTA injected headers indicating original RCPT TO:
      - Delivered-To
      - X-Original-To
      - Envelope-To
      - Mailing-list

    Returns a sorted list of unique lower-cased addresses.
    """
    addrs: set[str] = set(extract_addresses(msg, 'Bcc'))
    for hdr in ('Delivered-To', 'X-Original-To', 'Envelope-To', 'Mailing-list'):
        vals = msg.get_all(hdr, [])
        if not vals:
            continue
        for val in vals:
            for a in find_emails(val):
                addrs.add(a.lower())
    return sorted(addrs)


@lru_cache(maxsize=10)
def _load_template(name: str) -> str:
    """Load a template file from the package, caching its content.

    Uses relative path ``templates/<name>`` under this module's directory.
    """
    path = Path(__file__).parent / 'templates' / name
    try:
        text = path.read_text(encoding='utf-8')
    except Exception as e:  # pragma: no cover simple IO error path
        logger.error("Failed reading template %s: %s", name, e)
        text = ''
    return text


def render_email_html(
    subject: str,
    body_html: str,
    from_email: str,
    ticket_id: Optional[str] = None,
    *,
    creator_name: Optional[str] = None,
) -> str:
    """Wrap body HTML in a styled template including header/footer.

    Args:
        subject: Email subject line.
        body_html: Inner HTML content (already sanitized/escaped as needed).
        from_email: Actual From address used (to derive team name branding fallback).
        ticket_id: Optional ticket identifier to show beneath brand name.
        creator_name: Optional name of the Notion user who created the card/page. If provided it will
            be appended to the signature line ("Best regards") to give a more personal feel.

    Returns:
        Full HTML document string.
    """
    support_name = f'{BRAND_NAME} {from_email.split("@", 1)[0].capitalize()} Team'
    # Compose display name for signature: Prefer creator_name if supplied; otherwise brand/team.
    if creator_name:
        # Sanitize/strip to avoid accidental HTML; keep simple.
        safe_creator = html_lib.escape(creator_name.strip())
        signature_line = f"{safe_creator} — {html_lib.escape(support_name)}"
    else:
        signature_line = html_lib.escape(support_name)

    # Load external CSS & HTML wrapper
    styles_raw = _load_template('email_styles.css')
    wrapper_raw = _load_template('email_wrapper.html')

    icon_html = (
        f'<img class="brand-icon" src="{html_lib.escape(BRAND_ICON_URL)}" '  # nosec B703 content path
        f'alt="{html_lib.escape(BRAND_NAME)} logo" />'
        if BRAND_ICON_URL else ''
    )
    ticket_text = f"Ticket [{html_lib.escape(ticket_id)}]" if ticket_id else ''

    html_out = wrapper_raw.format(
        SUBJECT_ESC=html_lib.escape(subject),
        STYLES_ESC=styles_raw,  # stylesheet itself already safe static content
        ICON_HTML=icon_html,
        BRAND_NAME_ESC=html_lib.escape(BRAND_NAME),
        TICKET_TEXT_ESC=html_lib.escape(ticket_text),
        BODY_HTML=body_html,
        SIGNATURE_ESC=signature_line,
        FOOTER_TEXT_ESC=html_lib.escape(EMAIL_FOOTER_TEXT),
    )
    return html_out


def _derive_plain_text(html_body: str) -> str:
    """Very small HTML->text conversion for alternative part.

    We intentionally keep this minimal (strip tags, preserve block spacing).
    """
    text = html_body
    text = re.sub(r"</(p|h\d|li|blockquote|div|tr)>", "\n", text, flags=re.I)
    text = re.sub(r"<br ?/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: List[str] = []
    for line in lines:
        if line.strip():
            cleaned.append(line)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    return "\n".join(cleaned).strip()


def send_email(
    subject: str,
    html_body: str,
    to_emails: List[str],
    cc_emails: Optional[List[str]] = None,
    from_email: Optional[str] = None,
    attachments: Optional[List[str]] = None,
    *,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    ticket_id: Optional[str] = None,
    creator_name: Optional[str] = None,
) -> None:
    """Send a multipart (plain + HTML) email with optional attachments.

    Args:
        subject: Email subject.
        html_body: HTML content (without wrapper styling; wrapper added internally).
        to_emails: Primary recipient list.
        cc_emails: Optional CC recipients.
        from_email: Override From address (defaults to gmail_user).
        attachments: Optional list of filesystem paths to attach.
    """
    if not to_emails:
        logger.warning("No TO recipients provided; skipping email send")
        return
    # Basic subject hardening: collapse whitespace, strip newlines/carriage returns to avoid header injection
    subject = re.sub(r"[\r\n]+", " ", subject).strip()
    msg = MIMEMultipart()
    # Determine threading headers precedence: explicit in_reply_to > thread_id fallback
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        # Combine provided references with parent if not already present
        refs_combined = (references or '').strip()
        if in_reply_to not in refs_combined:
            refs_combined = (refs_combined + " " + in_reply_to).strip()
        msg["References"] = refs_combined[:2000]
    actual_from = from_email or GMAIL_USER
    msg["From"] = actual_from
    msg["Reply-To"] = actual_from
    msg["To"] = ", ".join(to_emails)
    if cc_emails:
        msg["Cc"] = ", ".join(cc_emails)
    msg["Subject"] = ('Re: ' if in_reply_to else '') + subject
    # Ensure a Message-ID header (allow override)

    # make_msgid auto-selects domain from the address if possible
    msg["Message-ID"] = make_msgid(domain='mail.gmail.com')
    full_html = render_email_html(subject, html_body, actual_from, ticket_id=ticket_id, creator_name=creator_name)
    plain = _derive_plain_text(full_html)
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(plain, 'plain'))
    alt.attach(MIMEText(full_html, 'html'))
    msg.attach(alt)

    files_attached: List[str] = []
    if attachments:
        for path in attachments:
            if not path or not os.path.isfile(path):
                logger.warning("Attachment path invalid or missing: %s", path)
                continue
            ctype, encoding = mimetypes.guess_type(path)
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            try:
                with open(path, "rb") as f:
                    part = MIMEApplication(f.read(), _subtype=subtype)
                part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(path))
                msg.attach(part)
                files_attached.append(path)
            except Exception as e:  # pragma: no cover simple logging
                logger.error("Failed attaching %s: %s", path, e)

    all_recipients = to_emails + (cc_emails or [])
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(actual_from, all_recipients, msg.as_string())
    logger.info("Email sent to %s cc=%s attachments=%d", to_emails, cc_emails, len(files_attached))
