from .fixtures import *  # noqa


async def test_link_round_trip_idempotent():
    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"plain_text": "Visit ", "text": {"content": "Visit "}},
                    {
                        "plain_text": "OpenAI",
                        "text": {"content": "OpenAI", "link": {"url": "https://openai.com"}},
                    },
                    {"plain_text": " now", "text": {"content": " now"}},
                ]
            },
        }
    ]
    html1 = await nu.blocks_to_html(blocks)
    blocks2 = await nu.html_to_blocks(html1)
    html2 = await nu.blocks_to_html(blocks2)
    # Link preserved
    assert 'href="https://openai.com"' in html2
    # Structure preserved (single paragraph containing OpenAI anchor)
    assert html1.count('<a ') == html2.count('<a ')
    assert html2.startswith('<p>')
