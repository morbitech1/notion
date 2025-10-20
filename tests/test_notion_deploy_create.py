import os
from pathlib import Path

from .fixtures import *  # noqa


async def test_notion_deploy_creates_missing(monkeypatch):
    # Prepare .env file
    # Use repository root (deploy.py resides in root); derive from this test file path
    env_path = Path(__file__).resolve().parent.parent / '.env'
    env_content = '\n'.join([
        'NOTION_TOKEN=secret',
        'NOTION_PARENT_PAGE_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        # Intentionally stale/placeholder support DB id (will trigger creation path)
        'NOTION_SUPPORT_CASES_DB_ID=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    ]) + '\n'
    env_path.write_text(env_content, encoding='utf-8')
    os.environ['NOTION_TOKEN'] = 'secret'
    os.environ['NOTION_PARENT_PAGE_ID'] = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    os.environ['NOTION_SUPPORT_CASES_DB_ID'] = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

    created = {}
    patched = {}

    async def fake_fetch_schema(db_id: str):
        # Return empty schema first call, then pretend creation populated properties
        return {}

    async def fake_create_db(parent_page_id: str, title: str, properties: dict, icon_emoji: str | None = None):
        created['title'] = title
        created['properties'] = properties
        created['icon_emoji'] = icon_emoji
        return {'id': 'newdbid1234567890'}

    async def fake_patch_props(db_id: str, properties: dict):
        patched['properties'] = properties
        return True

    monkeypatch.setattr(nu.api, 'fetch_database_schema', fake_fetch_schema)
    monkeypatch.setattr(nu.api, 'create_database_async', fake_create_db)
    monkeypatch.setattr(nu.api, 'patch_database_properties', fake_patch_props)
    await nud.notion_deploy_async()
    # Verify .env updated with new database id
    updated_env = env_path.read_text(encoding='utf-8')
    assert 'NOTION_SUPPORT_CASES_DB_ID=newdbid1234567890' in updated_env

    assert created, 'Expected database creation to occur'
    assert 'properties' in created
    assert patched, 'Expected properties patch after creation'
