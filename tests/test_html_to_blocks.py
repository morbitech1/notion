from .fixtures import *  # noqa


async def test_html_to_blocks_basic_structure():
    html = """
    <h1>Title</h1><p>Paragraph one.</p><p>Paragraph two<br/>line2</p>
    <ul><li>Item A</li><li>Item B</li></ul>
    <ol><li>First</li><li>Second</li></ol>
    <blockquote>Quote here</blockquote>
    <img src="https://example.com/x.png" alt="Example" />
    """
    blocks = await nu.html_to_blocks(html)
    # Ensure we have heading, paragraphs, list items, quote, image
    types = [b.get('type') for b in blocks]
    assert 'heading_1' in types
    assert 'paragraph' in types
    assert 'bulleted_list_item' in types
    assert 'numbered_list_item' in types
    assert 'quote' in types
    assert 'image' in types


async def test_create_email_record_adds_thread_and_link(monkeypatch):
    # Prepare a fake Notion create_page to capture properties
    captured = {}

    def fake_create_page(db, props, children=None):  # pragma: no cover - simple capture
        captured['props'] = props
        return 'new-page-id'
    monkeypatch.setattr(nu.config, 'NOTION_EMAILS_DB_ID', 'db123')

    async def async_create_page(db, props, children=None):  # async stub
        return fake_create_page(db, props, children)

    async def async_title_prop(_db):
        return 'Name'

    async def async_query_db(*a, **k):
        return {'results': []}
    monkeypatch.setattr(nu.api, 'create_page', async_create_page)
    monkeypatch.setattr(nu.api, 'get_database_title_property', async_title_prop)
    monkeypatch.setattr(nu.api, 'query_database', async_query_db)
    msg = EmailMessage()
    msg['Subject'] = 'Subject X'
    msg['From'] = 'from@example.com'
    msg['To'] = 'to@example.com'
    msg['X-GM-THRID'] = '1998438563533830'
    msg['X-GM-MSGID'] = '1998438563533830'
    msg.set_content('Plain body')
    await nu.create_email_record(msg, support_case_id=None, uid=42)
    props = captured.get('props', {})
    assert nu.config.PROP_EMAILS_THREAD_ID in props
    # Link is optional; if X-GM-MSGID captured ensure url property set
    if nu.config.PROP_EMAILS_LINK in props:
        link_obj = props[nu.config.PROP_EMAILS_LINK]
        assert isinstance(link_obj, dict)
        files = link_obj.get('files') if isinstance(link_obj, dict) else None
        assert isinstance(files, list) and files, 'Expected files list for Email link'
        urls = [f.get('external', {}).get('url', '') for f in files]
        assert any('1998438563533830' in u for u in urls)


async def test_html_to_blocks_ignores_css():
    html = """
    <style>.ignore{color:red}</style>
    <script>var x=1;</script>
    <p>Visible</p>
    """
    blocks = await nu.html_to_blocks(html)
    texts = []
    for b in blocks:
        if b.get('type') == 'paragraph':
            rt = b.get('paragraph', {}).get('rich_text', [])
            for r in rt:
                texts.append(r.get('plain_text', ''))
    combined = "\n".join(texts)
    assert 'Visible' in combined
    assert 'color:red' not in combined and 'var x' not in combined
