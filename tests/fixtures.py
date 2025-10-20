from email.message import EmailMessage, Message
from pathlib import Path
import pytest
import deploy as deploy_mod
import notion_automation.notion_utils as nu_mod
from notion_automation import imap_async as ia
from notion_automation import notion_utils as nu
from notion_automation import watch_email as we
from notion_automation import email_utils as eu
from notion_automation.notion_utils import config as nuc
from notion_automation.notion_utils import api as nua
from notion_automation.notion_utils import deploy as nud
from notion_automation.asyncio import run_async
import email
import os


class InMemoryNotion:
    """In-memory simulation of Notion data sources + pages for tests.

    Supports minimal subset:
      * create_database_async -> stores database + initial data source schema
      * fetch_database_schema -> returns data source object with properties
      * patch_database_properties -> merges new properties
      * create_page -> stores page with properties under data source id
      * query_database (data source) -> basic filter pass-through (page_size only)
    """

    def __init__(self):
        self.databases: dict[str, dict] = {}
        self.data_sources: dict[str, dict] = {}
        self.pages: dict[str, list[dict]] = {}
        self._next_id = 1

    def _gen_id(self) -> str:
        pid = f"ds{self._next_id:04d}"
        self._next_id += 1
        return pid

    async def create_database_async(self, parent_page_id: str, title: str, properties: dict, icon_emoji: str | None = None):  # noqa: D401
        db_id = f"db{self._gen_id()}"
        ds_id = f"{db_id}-src"
        db_obj = {
            "id": db_id,
            "title": [{"type": "text", "text": {"content": title}}],
            "icon": {"type": "emoji", "emoji": icon_emoji} if icon_emoji else None,
            "data_sources": [{"id": ds_id, "name": title}],
        }
        self.databases[db_id] = db_obj
        ds_obj = {
            "object": "data_source",
            "id": ds_id,
            "parent": {"type": "database_id", "database_id": db_id},
            "properties": properties.copy(),
            "title": [{"type": "text", "text": {"content": title}}],
        }
        self.data_sources[ds_id] = ds_obj
        self.pages.setdefault(ds_id, [])
        return db_obj

    async def fetch_database_schema(self, database_id: str):  # noqa: D401
        ids = {'emails': 'emailsdb', 'contacts': 'contactsdb', 'support_case': 'supportcasedb'}
        if database_id == 'emailsdb':
            return {'properties': nuc.expected_emails_properties(ids)}
        elif database_id == 'contactsdb':
            return {'properties': nuc.expected_contacts_properties(ids)}
        elif database_id == 'supportcasedb':
            return {'properties': nuc.expected_support_case_properties(ids)}
        elif database_id == 'contactsdb':
            return {'properties': nuc.expected_contacts_properties(ids)}
        elif database_id == 'partnersdb':
            return {'properties': nuc.expected_partners_properties(ids)}
        return self.data_sources.get(database_id, {})

    async def patch_database_properties(self, data_source_id: str, properties: dict):  # noqa: D401
        ds = self.data_sources.get(data_source_id)
        if not ds:
            return False
        props = ds.setdefault("properties", {})
        for k, v in properties.items():
            props[k] = v
        return True

    async def create_page(self, data_source_id: str, properties: dict, children=None):  # noqa: D401
        pid = f"page-{self._gen_id()}"
        page = {"id": pid, "properties": properties, "parent": {"data_source_id": data_source_id}}
        self.pages.setdefault(data_source_id, []).append(page)
        return pid

    async def query_database(self, data_source_id: str, payload=None):  # noqa: D401
        all_pages = self.pages.get(data_source_id, [])
        body = payload or {}
        page_size = body.get("page_size", 100)
        return {"results": all_pages[:page_size]}


@pytest.fixture(autouse=True)
def global_fixture(monkeypatch):
    env_path = Path(__file__).resolve().parent.parent / '.env'
    env_exists = env_path.exists()
    backup = ''
    if env_exists:
        backup = env_path.read_text(encoding='utf-8')
        for line in backup.splitlines():
            if line.count('=') == 1:
                key, _ = line.split('=', 1)
                key = key.strip('#').strip()
                os.environ.pop(key, None)
    env_path.write_text('', encoding='utf-8')
    monkeypatch.setattr('builtins.input', lambda prompt: 'UserInput')
    notion_memory = InMemoryNotion()
    monkeypatch.setattr(nu.api, 'create_database_async', notion_memory.create_database_async)
    monkeypatch.setattr(nu.api, 'fetch_database_schema', notion_memory.fetch_database_schema)
    monkeypatch.setattr(nu.api, 'patch_database_properties', notion_memory.patch_database_properties)
    monkeypatch.setattr(nu.api, 'create_page', notion_memory.create_page)
    monkeypatch.setattr(nu.api, 'query_database', notion_memory.query_database)
    try:
        yield
    finally:
        if backup:
            env_path.write_text(backup, encoding='utf-8')
        elif not env_exists:
            env_path.unlink(missing_ok=True)


__all__ = [
    'EmailMessage',
    'Message',
    'global_fixture',
    'deploy_mod',
    'nu_mod',
    'pytest',
    'ia',
    'nu',
    'we',
    'eu',
    'nuc',
    'nua',
    'run_async',
    'email',
    'nud',
]
