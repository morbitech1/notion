import pytest

from .fixtures import *  # noqa

# These tests assume environment variables for Notion DB IDs are set to fake IDs and that
# query_database / create_page are monkeypatched to operate on an in-memory store.

class InMemoryDB:
    def __init__(self):
        self.pages = {}  # id -> page dict
        self.schemas = {}
        self.next_id = 1

    def add_schema(self, db_id, title_prop):
        self.schemas[db_id] = {"properties": {title_prop: {"type": "title"}}}

    def create_page(self, db_id, props):
        pid = f"page-{self.next_id}"
        self.next_id += 1
        page = {"id": pid, "properties": props}
        self.pages.setdefault(db_id, []).append(page)
        return pid, page

    def query(self, db_id, payload):
        results = []
        candidates = self.pages.get(db_id, [])
        filt = (payload or {}).get('filter')
        # Very small subset evaluator for tests
        def title_equals(page, value, title_prop):
            tp = page['properties'].get(title_prop, {})
            if 'title' in tp:
                txt = ''.join(rt.get('plain_text','') for rt in tp['title'])
                return txt == value
            return False

        def rich_contains(page, prop, substr):
            p = page['properties'].get(prop, {})
            if 'rich_text' in p:
                txt = ''.join(rt.get('plain_text','') for rt in p['rich_text'])
                return substr in txt
            return False

        def relation_contains(page, prop, target):
            p = page['properties'].get(prop, {})
            rel = p.get('relation') if isinstance(p, dict) else None
            if isinstance(rel, list):
                return any(r.get('id') == target for r in rel if isinstance(r, dict))
            return False

        def eval_filter(page, f, title_prop):
            if not f:
                return True
            if 'or' in f:
                return any(eval_filter(page, sub, title_prop) for sub in f['or'])
            if 'and' in f:
                return all(eval_filter(page, sub, title_prop) for sub in f['and'])
            if 'property' in f:
                prop = f['property']
                if 'title' in f and 'equals' in f['title']:
                    return title_equals(page, f['title']['equals'], prop)
                if 'rich_text' in f and 'contains' in f['rich_text']:
                    return rich_contains(page, prop, f['rich_text']['contains'])
                if 'relation' in f and 'contains' in f['relation']:
                    return relation_contains(page, prop, f['relation']['contains'])
            return False

        for p in candidates:
            if eval_filter(p, filt, 'Name'):
                results.append(p)
        return {'results': results[: payload.get('page_size', 100)]}

memdb = InMemoryDB()
SUPPORT_DB = 'support-db'
EMAILS_DB = 'emails-db'
memdb.add_schema(SUPPORT_DB, 'Name')
memdb.add_schema(EMAILS_DB, 'Name')

@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setattr(nu.config, 'NOTION_SUPPORT_CASES_DB_ID', SUPPORT_DB)
    monkeypatch.setattr(nu.config, 'NOTION_EMAILS_DB_ID', EMAILS_DB)

@pytest.fixture(autouse=True)
def patch_network(monkeypatch):
    async def fake_fetch_schema(db_id):
        return memdb.schemas[db_id]
    async def fake_query(db_id, payload=None):
        return memdb.query(db_id, payload or {})
    async def fake_create_page(db_id, props, children=None):
        pid, page = memdb.create_page(db_id, props)
        return pid
    monkeypatch.setattr(nu.api, 'fetch_database_schema', fake_fetch_schema)
    monkeypatch.setattr(nu.api, 'query_database', fake_query)
    monkeypatch.setattr(nu.api, 'create_page', fake_create_page)

async def test_title_match_requires_message_id_overlap(monkeypatch):
    # Create existing support case with title "Issue A"
    title_prop = 'Name'
    case_props = {title_prop: {'title': nu._rt('Issue A')}}
    case_id, case_page = memdb.create_page(SUPPORT_DB, case_props)

    # Create an email linked to the support case with a message-id <abc@x>
    email_props = {
        title_prop: {'title': nu._rt('Email 1')},
        nu.config.PROP_EMAILS_SUPPORT_CASE_REL: {'relation': [{'id': case_id}]},
        nu.config.PROP_EMAILS_MESSAGE_ID: {'rich_text': nu._rt('<abc@x>')},
    }
    memdb.create_page(EMAILS_DB, email_props)

    # Incoming email with same subject but references that do NOT include <abc@x>
    subject = 'Issue A'
    refs = ['<zzz@x>']
    page = await nu.find_support_case(None, subject, title_prop, EMAILS_DB, refs)
    assert page is None, 'Should not match without message-id overlap'

    # Now include the overlapping message id
    refs2 = ['<zzz@x>', '<abc@x>']
    page2 = await nu.find_support_case(None, subject, title_prop, EMAILS_DB, refs2)
    assert page2 is not None and page2.get('id') == case_id

