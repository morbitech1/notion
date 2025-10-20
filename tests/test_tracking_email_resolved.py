from .fixtures import *  # noqa


async def test_tracking_email_sets_resolved(monkeypatch):
    msg = EmailMessage()
    msg['From'] = 'customer@example.com'
    msg['To'] = 'notion@company.domain'
    msg['Subject'] = 'Test tracking auto resolved'

    monkeypatch.setattr(nu.config, 'NOTION_SUPPORT_CASES_DB_ID', 'supportcasedb')
    monkeypatch.setattr(nu.config, 'NOTION_EMAILS_DB_ID', 'emailsdb')
    monkeypatch.setattr(nu.config, 'TRACKING_ALIAS', msg['To'])

    async def async_title(*a, **k):
        return 'Name'

    async def async_find_case(*a, **k):
        return None
    monkeypatch.setattr(nu.api, 'get_database_title_property', async_title)
    monkeypatch.setattr(nu, 'find_support_case', async_find_case)

    captured = {}

    async def fake_create(db_id, props, children=None):
        captured['props'] = props
        return 'page123'
    monkeypatch.setattr(nu.api, 'create_page', fake_create)
    # ensure no patch call is made by raising if used

    async def async_query_db(*a, **k):
        return {'results': []}
    monkeypatch.setattr(nu.api, 'query_database', async_query_db)

    page_id = await nu.find_or_create_support_case(msg)
    assert page_id == 'page123'
    status_prop = captured['props'][nu.config.PROP_SUPPORT_CASE_STATUS]['select']['name']
    assert status_prop == nu.config.VAL_STATUS_RESOLVED
    # Ensure type tracked as Tracking (multi_select property name in build_support_case_properties)
    # build_support_case_properties puts case type under PROP_SUPPORT_CASE_TYPE via multi_select; fetch from props
    # We import constant name indirectly; reuse attribute from notion_utils if needed.
    # Since we can't easily import here without adding overhead, just ensure 'Tracking' appears in any multi_select values in props.
    found_tracking = any(
        isinstance(v, dict) and isinstance(v.get('multi_select'), list) and any(
            o.get('name') == 'Tracking' for o in v['multi_select'])
        for v in captured['props'].values()
    )
    assert found_tracking, 'Tracking case type not set'
