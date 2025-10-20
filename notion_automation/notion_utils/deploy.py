
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

import notion_automation.notion_utils as nu

ENV_PATH = REPO_ROOT = Path(__file__).resolve().parent.parent.parent / '.env'


def user_confirm(prompt: str, cancel_msg: str = '[INFO] Aborted.') -> None:
    response = ''
    while response.lower() != 'y':
        if not sys.stdin.isatty():  # auto-confirm in non-interactive / force mode
            print(f"{prompt} (auto-confirmed)")
            return
        response = input(f"{prompt} (y/n): ").strip().lower()
        if response == 'n':
            print(cancel_msg)
            sys.exit(2)


def _persist_created_db_id(key: str, value: str) -> None:
    """Write created database id into .env (in-place update or append).

    Preserves file ordering; replaces line if key exists, else appends at end.
    """
    if not key or not value:
        return
    text = ENV_PATH.read_text(encoding='utf-8') if ENV_PATH.exists() else ''
    lines = text.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f'{key}=') or line.startswith(f'# {key}='):
            lines[i] = f'{key}={value}'
            replaced = True
            break
    if not replaced:
        lines.append(f'{key}={value}')
    ENV_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'[NOTION] Updated .env with {key}={value}')


def get_parent_page_id() -> str:
    """Prompt the user for a parent page id when interactive.

    In non-interactive (e.g. test / CI) environments the prompt is skipped and
    an empty string returned so callers can decide to bypass creation logic
    without triggering stdin read errors under pytest capture.
    """
    if not sys.stdin.isatty():  # non-interactive mode (tests / CI)
        print('[NOTION] Skipping parent page prompt (non-interactive).')
        return ''
    page_id = input(
        """
[NOTION] Parent page id missing;
    Please create a page in your Notion workspace to hold the databases,
    and share it with the integration (See: https://ibb.co/23cgBWJy).
    please provide the page id here (See: https://ibb.co/GQTHkD9j): """)
    page_id = page_id.strip().split('-')[-1]
    if not page_id:
        print('[NOTION] No parent page id provided, exiting.')
        exit(2)
    return page_id


def check_options(
    patch: dict[str, Any],
    existing: dict[str, Any],
    key: str,
    required: list[str],
) -> None:
    p = patch.get(key, {})
    e = existing.get(key, {})
    t = e.get('type', '')
    inner = e.get(t, {})
    cur_opts = inner.get('options', [])
    missing = [{'name': o} for o in required if not any(c.get('name') == o for c in cur_opts)]
    if missing or p:
        patch[key] = {
            p.get('type', t): {'options': cur_opts + missing}
        }


async def notion_deploy_async() -> None:
    """Audit configured Notion databases and ensure required properties exist.

    Rules:
      * If a *_DB_ID env var is set: assume DB already created; only ensure properties.
      * Missing properties are added via PATCH /v1/databases/{id}.
      * For existing status/multi_select properties, required option names are merged (no removal).
      * Relation properties are only added when their target database ID is available.
      * This action is intentionally excluded from --all.
    """
    # Resolve database IDs from environment
    ids = {}

    # DB id -> expected property mapping builder
    # NOTE: The second element in each tuple is the key used inside the ids mapping.
    # These MUST match the lookups performed inside expected_*_properties builders
    # (e.g. expected_emails_properties expects ids['support'] and ids['contacts']).
    plan: list[tuple[str, str, str, Callable[[dict[str, str]], dict[str, dict[str, Any]]]]] = [
        ('Partner', 'partner', 'NOTION_PARTNER_DB_ID', nu.config.expected_partners_properties),
        ('Contacts', 'contacts', 'NOTION_CONTACTS_DB_ID', nu.config.expected_contacts_properties),
        ('Support Cases', 'support_cases', 'NOTION_SUPPORT_CASES_DB_ID', nu.config.expected_support_case_properties),
        ('Emails', 'emails', 'NOTION_EMAILS_DB_ID', nu.config.expected_emails_properties),
        ('Replies', 'replies', 'NOTION_REPLIES_DB_ID', nu.config.expected_replies_properties),
    ]

    parent_page_id = os.getenv('NOTION_PARENT_PAGE_ID', '').strip()

    # Emoji icon mapping applied only during deploy-time creation (kept out of core API layer).
    icon_map: dict[str, str] = {
        'Partner': '🤝',
        'Contacts': '👥',
        'Support Cases': '🛠️',
        'Emails': '✉️',
        'Replies': '💬',
    }

    for label, db_key, env_key, builder in plan:
        print(f'[NOTION] Auditing {label} DB {db_key}...')
        db_id = ids[db_key] = os.getenv(env_key, '').strip()
        # Use top-level exported functions so tests can monkeypatch them via package module
        schema = await nu.api.fetch_database_schema(db_id)
        existing_props = schema.get('properties', {})
        # If fetch failed (empty) attempt creation when parent page id available
        if not existing_props:
            if not parent_page_id:
                # Attempt to create a root workspace parent page once (interactive only)
                parent_page_id = get_parent_page_id()
                _persist_created_db_id('NOTION_PARENT_PAGE_ID', parent_page_id)
            print(f'[NOTION] Schema empty for {label}. Attempting creation...')
            # Build initial properties (must include title prop) from expected builder
            expected_init = builder(ids)
            # Ensure at least one title property exists; fallback to Ticket ID turning into title
            if not any(v.get('title') for v in expected_init.values() if isinstance(v, dict)):
                # Add a generic title property
                expected_init['Name'] = {'title': {}}
            user_confirm(f'[NOTION] Create {label} database under parent page id {parent_page_id}?')
            # Include icon only at initial creation (optional param keeps API generic)
            emoji = icon_map.get(label)
            database = await nu.api.create_database_async(parent_page_id, label, expected_init, icon_emoji=emoji)
            new_id = database.get('id')
            if not new_id:
                print(f'[NOTION] Creation failed for {label}; Exiting.')
                exit(2)

            print(f'[NOTION] Created database {label} id={new_id}. Re-fetching schema...')
            _persist_created_db_id(env_key, new_id)
            ids[db_key] = db_id = new_id
            schema = await nu.api.fetch_database_schema(db_id)
            existing_props = schema.get('properties', {}) if isinstance(schema.get('properties'), dict) else {}
        existing_names = set(existing_props.keys())
        to_add: dict[str, dict[str, Any]] = {}
        expected = builder(ids)
        for pname, definition in expected.items():
            if pname not in existing_names:
                to_add[pname] = definition
        # Merge options for status/multi_select if missing required ones
        patch: dict[str, dict[str, Any]] = {}
        for k, v in expected.items():
            if k not in existing_props or existing_props[k].get('type') != v.get('type'):
                patch[k] = v
        # Status property options check (support cases only)
        if label == 'Support Cases':
            required = [nu.config.VAL_STATUS_OPEN, nu.config.VAL_STATUS_NEW_REPLY, nu.config.VAL_STATUS_RESOLVED]
            check_options(patch, existing_props, nu.config.PROP_SUPPORT_CASE_STATUS, required)
            required_type = [nu.config.VAL_TYPE_TECHNICAL, nu.config.VAL_TYPE_SUPPORT]
            check_options(patch, existing_props, nu.config.PROP_SUPPORT_CASE_TYPE, required_type)
        # Combine additions + option merges
        patch.update(to_add)
        if patch:
            user_confirm(f'[NOTION] Apply patch for {label} DB: {patch}?')
            await nu.api.patch_database_properties(db_id, patch)
        else:
            print(f'[NOTION] {label} DB already satisfies required properties.')
    print('[NOTION] Audit complete.')
