import os
from pathlib import Path

from .fixtures import *  # noqa


async def test_notion_deploy_creates_root_parent(monkeypatch):
    # Minimal .env without parent page id
    env_path = Path(__file__).resolve().parent.parent / '.env'
    env_path.write_text('NOTION_TOKEN=secret\nNOTION_SUPPORT_CASES_DB_ID=missingmissingmissingmissingmiss1234\n', encoding='utf-8')
    # Ensure parent page id not preset from prior tests
    os.environ.pop('NOTION_PARENT_PAGE_ID', None)
    os.environ['NOTION_TOKEN'] = 'secret'
    os.environ['NOTION_SUPPORT_CASES_DB_ID'] = 'missingmissingmissingmissingmiss1234'

    created_db = {}

    async def fake_fetch_schema(_):
        # Always empty to trigger creation path
        return {}

    def fake_get_parent_page_id():
        return 'parentpage1234567890'

    async def fake_create_db(parent_page_id: str, title: str, properties: dict, icon_emoji: str | None = None):
        # Accept either dynamically created parent or a leftover env (should be created one for this test)
        assert parent_page_id in {'parentpage1234567890'}
        created_db['title'] = title
        return {'id': 'newdbabcdef1234567890'}

    async def fake_patch_props(db_id: str, properties: dict):
        return True

    monkeypatch.setattr(nu.api, 'fetch_database_schema', fake_fetch_schema)
    monkeypatch.setattr(nud, 'get_parent_page_id', fake_get_parent_page_id)
    monkeypatch.setattr(nu.api, 'create_database_async', fake_create_db)
    monkeypatch.setattr(nu.api, 'patch_database_properties', fake_patch_props)

    await nud.notion_deploy_async()

    updated_env = env_path.read_text(encoding='utf-8')
    assert 'NOTION_PARENT_PAGE_ID=parentpage1234567890' in updated_env
    assert 'NOTION_SUPPORT_CASES_DB_ID=newdbabcdef1234567890' in updated_env
