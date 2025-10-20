from .fixtures import *  # noqa

async def test_blocks_to_html_stops_at_divider():
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Before"}]}},
        {"type": "divider", "divider": {}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "After"}]}},
    ]
    html = await nu.blocks_to_html(blocks)
    assert "Before" in html
    assert "After" not in html


async def test_blocks_to_html_image_style():
    blocks = [
        {
            "type": "image",
            "image": {
                "external": {"url": "https://example.com/image.png"},
                "caption": [{"plain_text": "An image"}],
            },
        }
    ]
    html = await nu.blocks_to_html(blocks)
    assert 'style="max-width:100%;height:auto;display:block;"' in html
