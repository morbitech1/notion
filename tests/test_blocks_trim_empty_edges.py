from .fixtures import *  # noqa


async def test_blocks_trim_empty_edges():
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": []}},  # leading empty
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Middle"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": []}},  # trailing empty
    ]
    html = await nu.blocks_to_html(blocks)
    # Should only contain the middle paragraph, no solitary <br /> at edges
    assert html.strip() == '<p>Middle</p>'

async def test_blocks_trim_all_empty():
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": []}},
        {"type": "paragraph", "paragraph": {"rich_text": []}},
    ]
    html = await nu.blocks_to_html(blocks)
    # All empty trimmed -> empty string
    assert html == ''