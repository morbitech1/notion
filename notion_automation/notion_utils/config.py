import os
import re
from typing import Optional

from notion_automation.types import Headers


def _env(name: str, default: str | None = None) -> str:
    '''Return environment variable value with required/optional semantics.

    If ``default`` is ``None`` the variable is treated as required and a KeyError
    will propagate if missing. Otherwise the provided default is used when the
    variable is absent.

    Args:
        name: Environment variable name.
        default: Default value (signals optional) or None for required.

    Returns:
        The resolved environment value or the default.
    '''
    if default is None:
        return os.environ[name]
    return os.getenv(name, default)

# Unified naming: NOTION_PROP_<DB>_<PROP>
# Databases (logical): SUPPORT_CASE, EMAILS, REPLIES (for pages -> email)


# Support Case DB
PROP_SUPPORT_CASE_NAME = _env('NOTION_PROP_SUPPORT_CASE_NAME', 'Name')
PROP_SUPPORT_CASE_STATUS = _env('NOTION_PROP_SUPPORT_CASE_STATUS', 'Status')
PROP_SUPPORT_CASE_TYPE = _env('NOTION_PROP_SUPPORT_CASE_TYPE', 'Type')
PROP_SUPPORT_CASE_TICKET_ID = _env('NOTION_PROP_SUPPORT_CASE_TICKET_ID', 'Ticket ID')
PROP_SUPPORT_CASE_PARTNER_REL = _env('NOTION_PROP_SUPPORT_CASE_PARTNER_REL', 'Partner')

# Email Log DB
PROP_EMAILS_NAME = _env('NOTION_PROP_EMAILS_NAME', 'Name')
PROP_EMAILS_TO = _env('NOTION_PROP_EMAILS_TO', 'To')
PROP_EMAILS_FROM = _env('NOTION_PROP_EMAILS_FROM', 'From')
PROP_EMAILS_CC = _env('NOTION_PROP_EMAILS_CC', 'CC')
PROP_EMAILS_SUPPORT_CASE_REL = _env('NOTION_PROP_EMAILS_SUPPORT_CASE_REL', 'Support Case')
PROP_EMAILS_UID = _env('NOTION_PROP_EMAILS_UID', 'Email UID')
PROP_EMAILS_THREAD_ID = _env('NOTION_PROP_EMAILS_THREAD_ID', 'Thread ID')
PROP_EMAILS_LINK = _env('NOTION_PROP_EMAILS_LINK', 'Email link')
PROP_EMAILS_ATTACHMENTS = _env('NOTION_PROP_EMAILS_ATTACHMENTS', 'Attachments')
PROP_EMAILS_MESSAGE_ID = _env('NOTION_PROP_EMAILS_MESSAGE_ID', 'Message ID')
PROP_EMAILS_REFERENCES = _env('NOTION_PROP_EMAILS_REFERENCES', 'References')
PROP_EMAILS_TICKET_ID = _env('NOTION_PROP_EMAILS_TICKET_ID', 'Ticket ID')
PROP_EMAILS_CONTACTS_REL = _env('NOTION_PROP_EMAILS_CONTACTS_REL', 'Contacts')  # Relation to Contacts DB

# Outbound (pages -> email) DB property names
PROP_REPLIES_FROM = _env('NOTION_PROP_REPLIES_FROM', 'From')
PROP_REPLIES_TO = _env('NOTION_PROP_REPLIES_TO', 'To')
PROP_REPLIES_CC = _env('NOTION_PROP_REPLIES_CC', 'CC')
PROP_REPLIES_ATTACHMENTS = _env('NOTION_PROP_REPLIES_ATTACHMENTS', 'Attachments')
PROP_REPLIES_SEND = _env('NOTION_PROP_REPLIES_SEND', 'Send email')
PROP_REPLIES_SENT = _env('NOTION_PROP_REPLIES_SENT', 'Email sent')
PROP_REPLIES_TICKET_ID = _env('NOTION_PROP_REPLIES_TICKET_ID', 'Ticket ID')
PROP_REPLIES_IN_REPLY_TO = _env('NOTION_PROP_REPLIES_IN_REPLY_TO', 'In-Reply-To')
PROP_REPLIES_REFERENCES = _env('NOTION_PROP_REPLIES_REFERENCES', 'References')
PROP_REPLIES_CREATED_BY = _env('NOTION_PROP_REPLIES_CREATED_BY', 'Created by')
PROP_REPLIES_INCLUDE_NAME = _env('NOTION_PROP_REPLIES_INCLUDE_NAME', 'Include name in signature')
PROP_REPLIES_EMAIL_REL = _env('NOTION_PROP_REPLIES_EMAIL_REL', 'Reply to')

# Contacts DB (used to enrich Support Case with Partner relation based on sender email)
PROP_CONTACTS_EMAIL = _env('NOTION_PROP_CONTACTS_EMAIL', 'Email')
PROP_CONTACTS_PARTNER_REL = _env('NOTION_PROP_CONTACTS_PARTNER_REL', 'Partner')

# Select option values
VAL_STATUS_OPEN = _env('NOTION_VAL_SUPPORT_CASE_STATUS_OPEN', 'Open')
VAL_STATUS_NEW_REPLY = _env('NOTION_VAL_SUPPORT_CASE_STATUS_NEW_REPLY', 'New reply')
VAL_STATUS_RESOLVED = _env('NOTION_VAL_SUPPORT_CASE_STATUS_RESOLVED', 'Resolved')
VAL_TYPE_TECHNICAL = _env('NOTION_VAL_SUPPORT_CASE_TYPE_TECHNICAL', 'Technical')
VAL_TYPE_SUPPORT = _env('NOTION_VAL_SUPPORT_CASE_TYPE_SUPPORT', 'Support')

COMPANY_DOMAIN = os.getenv('COMPANY_DOMAIN', '').strip().lower()
ENGINEERING_ALIAS = os.getenv('ENGINEERING_EMAIL', f'engineering@{COMPANY_DOMAIN}').strip().lower()
SUPPORT_ALIAS = os.getenv('SUPPORT_EMAIL', f'support@{COMPANY_DOMAIN}').strip().lower()
TRACKING_ALIAS = os.getenv('TRACKING_EMAIL', f'notion@{COMPANY_DOMAIN}').strip().lower()
NOTION_SUPPORT_CASES_DB_ID = os.getenv('NOTION_SUPPORT_CASES_DB_ID', '')
NOTION_EMAILS_DB_ID = os.getenv('NOTION_EMAILS_DB_ID', '')
NOTION_CONTACTS_DB_ID = os.getenv('NOTION_CONTACTS_DB_ID', '')
TICKET_REGEX = re.compile(r'\[(\d{10})\]')


# ---------------- Constant Notion headers ----------------
NOTION_VERSION = '2022-06-28'
NOTION_TOKEN = os.getenv('NOTION_TOKEN')
HEADERS: Optional[Headers] = {
    **({'Authorization': f'Bearer {NOTION_TOKEN}'} if NOTION_TOKEN else {}),
    'Notion-Version': NOTION_VERSION,
    'Content-Type': 'application/json',
}


def expected_partners_properties(ids: dict[str, str]) -> dict[str, dict]:
    return {}


def expected_contacts_properties(ids: dict[str, str]) -> dict[str, dict]:
    return {
        PROP_CONTACTS_EMAIL: {'type': 'email', 'email': {}},
        PROP_CONTACTS_PARTNER_REL: {
            'type': 'relation',
            'relation': {
                'database_id': ids['partner'],
                'dual_property': {'name': 'Contacts'}  # reciprocal property on Partner DB
            }
        }
    }


def expected_support_case_properties(ids: dict[str, str]) -> dict[str, dict]:
    """Return expected Support Case DB property definitions.

    Relation to Partner added only when contacts_db_id provided.
    """
    props: dict[str, dict] = {
        PROP_SUPPORT_CASE_STATUS: {
            'type': 'select',
            'select': {
                'options': [
                    {'name': VAL_STATUS_OPEN},
                    {'name': VAL_STATUS_NEW_REPLY},
                    {'name': VAL_STATUS_RESOLVED},
                ]
            }
        },
        PROP_SUPPORT_CASE_TYPE: {
            'type': 'multi_select',
            'multi_select': {
                'options': [
                    {'name': VAL_TYPE_TECHNICAL},
                    {'name': VAL_TYPE_SUPPORT},
                ]
            }
        },
        PROP_SUPPORT_CASE_TICKET_ID: {'type': 'rich_text', 'rich_text': {}},
    }
    # Add Partner relation only when Partner DB id already known (avoid KeyError during first creation)
    partner_id = ids.get('partner')
    if partner_id:
        props[PROP_SUPPORT_CASE_PARTNER_REL] = {
            'type': 'relation',
            'relation': {
                'database_id': partner_id,
                'dual_property': {'name': 'Support Cases'}
            }
        }
    return props


def expected_emails_properties(ids: dict[str, str]) -> dict[str, dict]:
    return {
        PROP_EMAILS_TO: {'type': 'multi_select', 'multi_select': {'options': []}},
        PROP_EMAILS_FROM: {'type': 'email', 'email': {}},
        PROP_EMAILS_CC: {'type': 'multi_select', 'multi_select': {'options': []}},
        PROP_EMAILS_UID: {'type': 'rich_text', 'rich_text': {}},
        PROP_EMAILS_THREAD_ID: {'type': 'rich_text', 'rich_text': {}},
        PROP_EMAILS_LINK: {'type': 'files', 'files': {}},
        PROP_EMAILS_ATTACHMENTS: {'type': 'files', 'files': {}},
        PROP_EMAILS_MESSAGE_ID: {'type': 'rich_text', 'rich_text': {}},
        PROP_EMAILS_REFERENCES: {'type': 'rich_text', 'rich_text': {}},
        PROP_EMAILS_CONTACTS_REL: {
            'type': 'relation',
            'relation': {
                'database_id': ids['contacts'],
                'dual_property': {'name': 'Emails'}
            }
        },
        PROP_EMAILS_SUPPORT_CASE_REL: {
            'type': 'relation',
            'relation': {
                'database_id': ids['support_cases'],
                'dual_property': {'name': 'Emails'}
            }
        },
        PROP_EMAILS_TICKET_ID: {
            'type': 'rollup',
            'rollup': {
                'relation_property_name': PROP_EMAILS_SUPPORT_CASE_REL,
                'rollup_property_name': PROP_SUPPORT_CASE_TICKET_ID,
                'function': 'show_original'
            }
        },
    }


def expected_replies_properties(ids: dict[str, str]) -> dict[str, dict]:
    return {
        PROP_REPLIES_FROM: {'type': 'rich_text', 'rich_text': {}},
        PROP_REPLIES_TO: {'type': 'rich_text', 'rich_text': {}},
        PROP_REPLIES_CC: {'type': 'rich_text', 'rich_text': {}},
        PROP_REPLIES_ATTACHMENTS: {'type': 'files', 'files': {}},
        PROP_REPLIES_SEND: {'type': 'checkbox', 'checkbox': {}},
        PROP_REPLIES_SENT: {'type': 'checkbox', 'checkbox': {}},
        PROP_REPLIES_CREATED_BY: {'type': 'created_by', 'created_by': {}},
        PROP_REPLIES_INCLUDE_NAME: {'type': 'checkbox', 'checkbox': {}},
        PROP_REPLIES_TICKET_ID: {'type': 'rich_text', 'rich_text': {}},
        PROP_REPLIES_EMAIL_REL: {
            'type': 'relation',
            'relation': {
                # ids key aligns with deploy.plan entry for Emails
                'database_id': ids['emails'],
                'dual_property': {'name': 'Replies'}
            }
        },
        # Ticket ID / In-Reply-To / References now rollups from related Email record
        PROP_REPLIES_REFERENCES: {
            'type': 'rollup',
            'rollup': {
                'relation_property_name': PROP_REPLIES_EMAIL_REL,
                'rollup_property_name': PROP_EMAILS_REFERENCES,
                'function': 'show_original'
            }
        },
        PROP_REPLIES_IN_REPLY_TO: {
            'type': 'rollup',
            'rollup': {
                'relation_property_name': PROP_REPLIES_EMAIL_REL,
                'rollup_property_name': PROP_EMAILS_MESSAGE_ID,
                'function': 'show_original'
            }
        },
    }
