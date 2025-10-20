from .fixtures import *  # noqa


async def test_blocks_to_html_link_and_inline_code():
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
                    {"plain_text": " and some ", "text": {"content": " and some "}},
                    {
                        "plain_text": "code",
                        "text": {"content": "code"},
                        "annotations": {"code": True},
                    },
                    {"plain_text": ".", "text": {"content": "."}},
                ]
            },
        }
    ]
    html = await nu.blocks_to_html(blocks)
    assert '<a href="https://openai.com"' in html
    assert '<code>code</code>' in html
