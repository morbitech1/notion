from .fixtures import *  # noqa

async def test_blocks_to_html_text_annotations_and_callout_and_multiline_quote():
    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "Bold Italic Underline Strikethrough",
                        "text": {"content": "Bold Italic Underline Strikethrough"},
                        "annotations": {"bold": True, "italic": True, "underline": True, "strikethrough": True},
                    },
                    {"plain_text": " normal", "text": {"content": " normal"}},
                ]
            },
        },
        {
            "type": "quote",
            "quote": {
                "rich_text": [
                    {"plain_text": "Line one", "text": {"content": "Line one"}},
                    {"plain_text": "\nLine two", "text": {"content": "\nLine two"}},
                ]
            },
        },
        {
            "type": "callout",
            "callout": {
                "rich_text": [
                    {"plain_text": "Remember this", "text": {"content": "Remember this"}},
                ],
                "icon": {"type": "emoji", "emoji": "⚠️"},
            },
        },
    ]
    html = await nu.blocks_to_html(blocks)
    # Annotation nesting: ensure each tag present
    assert '<strong>' in html and '</strong>' in html
    assert '<em>' in html and '</em>' in html
    assert '<u>' in html and '</u>' in html
    assert '<del>' in html and '</del>' in html
    # Multi-line quote should preserve line breaks as <br />
    assert '<blockquote>' in html and 'Line one' in html and 'Line two' in html and '<br />' in html
    # Callout wrapper and icon
    assert '<div class="callout">' in html and '⚠️' in html