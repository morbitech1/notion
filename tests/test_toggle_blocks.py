from .fixtures import *  # noqa

async def test_html_toggle_roundtrip():
    html = '<p>Intro</p><div class="toggle"><div class="toggle-summary">More info</div><div class="toggle-content"><p>Hidden A</p><p>Hidden B</p></div></div>'
    blocks = await nu.html_to_blocks(html)
    # Expect a paragraph + toggle
    assert len(blocks) == 2
    assert blocks[0]['type'] == 'paragraph'
    assert blocks[1]['type'] == 'toggle'
    assert blocks[1]['toggle']['rich_text'][0]['plain_text'] == 'More info'
    rendered = await nu.blocks_to_html(blocks)
    assert '<div class="toggle"><div class="toggle-summary">More info</div>' in rendered
    assert 'Hidden A' in rendered and 'Hidden B' in rendered

async def test_previous_thread_wrapped_toggle():
    # Simulate email body HTML including quoted reply line
    msg = EmailMessage()
    msg['Subject'] = 'Test toggle'
    html = '<div>New reply content</div><div>On Tue, Oct 15 John Doe wrote:</div><div>Older content line 1</div><div>Older content line 2</div>'
    msg.set_content('Plain fallback')
    msg.add_alternative(html, subtype='html')
    blocks = await nu.build_email_content_blocks(msg)
    # Should produce a toggle wrapping older content
    assert any(b.get('type') == 'toggle' for b in blocks)
    toggle = next(b for b in blocks if b.get('type') == 'toggle')
    assert toggle['toggle']['rich_text'][0]['plain_text'] == 'Previous thread'
    # Children should include at least the quoted line
    child_texts = []
    for ch in toggle['toggle']['children']:
        sec = ch.get(ch.get('type'), {}) if isinstance(ch.get(ch.get('type')), dict) else ch.get(ch.get('type'), {})
        rt = sec.get('rich_text') if isinstance(sec, dict) else []
        for r in rt or []:
            if isinstance(r, dict) and isinstance(r.get('plain_text'), str):
                child_texts.append(r['plain_text'])
    assert any('Older content line 1' in c for c in child_texts)
