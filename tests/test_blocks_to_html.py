from .fixtures import *  # noqa


async def test_blocks_to_html_basic():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Title"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "First paragraph."}]}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Item 1"}]}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Item 2"}]}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"plain_text": "Num 1"}]}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"plain_text": "Num 2"}]}},
        {"type": "quote", "quote": {"rich_text": [{"plain_text": "Quoted text"}]}}
    ]
    html = await nu.blocks_to_html(blocks)
    # Basic structural assertions
    assert '<h1>Title</h1>' in html
    assert '<p>First paragraph.</p>' in html
    # Bullet list should wrap items with <ul>
    assert '<ul>' in html and '</ul>' in html and '<li>Item 1</li>' in html
    # Ordered list should wrap items with <ol>
    assert '<ol>' in html and '</ol>' in html and '<li>Num 2</li>' in html
    # Quote present
    assert '<blockquote>Quoted text</blockquote>' in html
    # Ensure lists are closed before next block type transitions
    # (ul then ol order) by checking closing tags appear before following list type
    ul_close_index = html.index('</ul>')
    ol_index = html.index('<ol>')
    assert ul_close_index < ol_index
