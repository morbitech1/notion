import json
import os
from pathlib import Path
import pytest

# Import fixtures module to register its autouse/session fixtures without star import side-effects.
from tests import fixtures  # noqa: F401
from notion_automation.notion_utils import config as nuc
from notion_automation.notion_utils import api as nua
from notion_automation.asyncio import run_async


class _LocalInMemoryNotion:
    def __init__(self):
        self.databases = {}
        self.data_sources = {}
        self.pages = {}
        self._next = 1

    def _gen(self):
        v = f"ds{self._next:04d}"
        self._next += 1
        return v

    async def create_database_async(self, parent_page_id: str, title: str, properties: dict, icon_emoji: str | None = None):
        db_id = f"db{self._gen()}"
        ds_id = f"{db_id}-src"
        db_obj = {"id": db_id, "data_sources": [{"id": ds_id, "name": title}]}
        self.databases[db_id] = db_obj
        self.data_sources[ds_id] = {"id": ds_id, "parent": {"database_id": db_id}, "properties": properties}
        self.pages.setdefault(ds_id, [])
        return db_obj

    async def fetch_database_schema(self, data_source_id: str):
        return self.data_sources.get(data_source_id, {})

    async def patch_database_properties(self, data_source_id: str, properties: dict):
        ds = self.data_sources[data_source_id]
        ds["properties"].update(properties)
        return True

    async def create_page(self, data_source_id: str, properties: dict, children=None):
        pid = f"page-{self._gen()}"
        page = {"id": pid, "properties": properties}
        self.pages[data_source_id].append(page)
        return pid

    async def query_database(self, data_source_id: str, payload=None):
        return {"results": self.pages.get(data_source_id, [])}


@pytest.fixture
def notion_memory(monkeypatch):
    mem = _LocalInMemoryNotion()
    monkeypatch.setattr(nua, 'create_database_async', mem.create_database_async)
    monkeypatch.setattr(nua, 'fetch_database_schema', mem.fetch_database_schema)
    monkeypatch.setattr(nua, 'patch_database_properties', mem.patch_database_properties)
    monkeypatch.setattr(nua, 'create_page', mem.create_page)
    monkeypatch.setattr(nua, 'query_database', mem.query_database)
    return mem


@pytest.mark.parametrize("ds_label, env_var", [
    ("Support Cases", "NOTION_SUPPORT_CASES_DATA_SOURCE_ID"),
    ("Emails", "NOTION_EMAILS_DATA_SOURCE_ID"),
    ("Replies", "NOTION_REPLIES_DATA_SOURCE_ID"),
])
def test_notion_deploy_patches_missing(monkeypatch, notion_memory, ds_label, env_var):
    """Validate property patch logic using in-memory data source.

    We simulate an existing data source with only the Ticket ID property and
    then invoke the expected_* builder to compute missing properties, applying
    a patch via the patched notion API.
    """
    # Create minimal data source (database + data source) in memory
    created_db = run_async(nua.create_database_async('parent', ds_label, {nuc.PROP_SUPPORT_CASE_TICKET_ID: {'rich_text': {}, 'type': 'rich_text'}}, icon_emoji='📄'))
    # Extract data source id from memory (last created)
    ds_id = created_db['data_sources'][0]['id']
    os.environ[env_var] = ds_id

    # Build ids mapping akin to deploy.plan for relation resolution
    ids_map = {
        'partner': os.environ.get('NOTION_PARTNER_DATA_SOURCE_ID', ''),
        'contacts': os.environ.get('NOTION_CONTACTS_DATA_SOURCE_ID', ''),
        'support_cases': os.environ.get('NOTION_SUPPORT_CASES_DATA_SOURCE_ID', ''),
        'emails': os.environ.get('NOTION_EMAILS_DATA_SOURCE_ID', ''),
        'replies': os.environ.get('NOTION_REPLIES_DATA_SOURCE_ID', ''),
    }

    # Select correct builder
    builder_map = {
        'Support Cases': nuc.expected_support_case_properties,
        'Emails': nuc.expected_emails_properties,
        'Replies': nuc.expected_replies_properties,
    }
    builder = builder_map[ds_label]
    existing_schema = run_async(nua.fetch_database_schema(ds_id))
    existing_props = existing_schema.get('properties', {})
    expected = builder(ids_map)
    missing = {k: v for k, v in expected.items() if k not in existing_props}
    if missing:
        run_async(nua.patch_database_properties(ds_id, missing))
    updated_schema = run_async(nua.fetch_database_schema(ds_id))
    for k in expected.keys():
        assert k in updated_schema['properties'], f"Expected property {k} to be present after patch"
