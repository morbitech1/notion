from .fixtures import *  # noqa

async def test_roundtrip_color_annotations():
    # Construct blocks with varied color annotations
    sample_blocks = [
        {
            "type": "paragraph",
            "paragraph": {"rich_text": [
                {"plain_text": "Red", "text": {"content": "Red"}, "annotations": {"bold": False,
                                                                                  "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "red"}},
                {"plain_text": " Default", "text": {"content": " Default"}, "annotations": {"bold": False,
                                                                                            "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"}},
                {"plain_text": " BlueBold", "text": {"content": " BlueBold"}, "annotations": {"bold": True,
                                                                                              "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "blue"}},
            ]}
        },
        {
            "type": "heading_2",
            "heading_2": {"rich_text": [
                {"plain_text": "HeadingGreen", "text": {"content": "HeadingGreen"}, "annotations": {"bold": True,
                                                                                                    "italic": True, "strikethrough": False, "underline": False, "code": False, "color": "green"}},
            ]}
        },
        {
            "type": "quote",
            "quote": {"rich_text": [
                {"plain_text": "QuoteGray", "text": {"content": "QuoteGray"}, "annotations": {"bold": False,
                                                                                              "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "gray"}},
            ]}
        },
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [
                {"plain_text": "ListPink", "text": {"content": "ListPink"}, "annotations": {"bold": False,
                                                                                            "italic": True, "strikethrough": False, "underline": False, "code": False, "color": "pink"}},
            ]}
        },
    ]

    html = await nu.blocks_to_html(sample_blocks)
    regenerated = await nu.html_to_blocks(html)

    # Helper to pull first block of a given type
    def find_block(t):
        return next((b for b in regenerated if b.get("type") == t), None)

    para = find_block("paragraph")
    assert para, "Paragraph block missing after roundtrip"
    colors_para = [r.get("annotations", {}).get("color") for r in para["paragraph"]["rich_text"]]
    assert "red" in colors_para
    assert "default" in colors_para
    assert "blue" in colors_para

    heading = find_block("heading_2")
    assert heading, "Heading block missing"
    heading_colors = [r.get("annotations", {}).get("color") for r in heading["heading_2"]["rich_text"]]
    assert heading_colors[0] == "green"

    quote = find_block("quote")
    assert quote, "Quote block missing"
    quote_colors = [r.get("annotations", {}).get("color") for r in quote["quote"]["rich_text"]]
    assert quote_colors[0] == "gray"

    bl_item = find_block("bulleted_list_item")
    assert bl_item, "Bulleted list item missing"
    list_colors = [r.get("annotations", {}).get("color") for r in bl_item["bulleted_list_item"]["rich_text"]]
    assert list_colors[0] == "pink"
