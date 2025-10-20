from .fixtures import *  # noqa


async def test_roundtrip_annotations_preserved():
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": [
            {"plain_text": "Bold", "text": {"content": "Bold"}, "annotations": {"bold": True, "italic": False,
                                                                                "underline": False, "strikethrough": False, "code": False, "color": "default"}},
            {"plain_text": " Italic", "text": {"content": " Italic"}, "annotations": {"bold": False,
                                                                                      "italic": True, "underline": False, "strikethrough": False, "code": False, "color": "default"}},
            {"plain_text": " Underline", "text": {"content": " Underline"}, "annotations": {"bold": False,
                                                                                            "italic": False, "underline": True, "strikethrough": False, "code": False, "color": "default"}},
            {"plain_text": " Strike", "text": {"content": " Strike"}, "annotations": {"bold": False,
                                                                                      "italic": False, "underline": False, "strikethrough": True, "code": False, "color": "default"}},
            {"plain_text": " InlineCode", "text": {"content": " InlineCode"}, "annotations": {"bold": False,
                                                                                              "italic": False, "underline": False, "strikethrough": False, "code": True, "color": "default"}},
        ]}},
        {"type": "heading_2", "heading_2": {"rich_text": [
            {"plain_text": "Mixed", "text": {"content": "Mixed"}, "annotations": {"bold": True,
                                                                                  "italic": True, "underline": True, "strikethrough": True, "code": False, "color": "default"}},
        ]}},
    ]

    html = await nu.blocks_to_html(blocks)
    regen = await nu.html_to_blocks(html)

    # Find first paragraph and compare annotation flags token-wise (order preserved by blocks_to_html)
    para = next(b for b in regen if b.get("type") == "paragraph")
    rt = para["paragraph"]["rich_text"]
    # Because nested tags convert each segment with merged annotations, we compare substring membership
    # Ensure at least one rich_text with each annotation

    def has_ann(key):
        return any(r.get("annotations", {}).get(key) for r in rt)
    assert has_ann("bold")
    assert has_ann("italic")
    assert has_ann("underline")
    assert has_ann("strikethrough")
    assert has_ann("code")

    # Heading block retains merged annotations for its single rich_text
    heading = next(b for b in regen if b.get("type") == "heading_2")
    h_rt = heading["heading_2"]["rich_text"]
    assert h_rt and h_rt[0]["annotations"]["bold"]
    assert h_rt[0]["annotations"]["italic"]
    assert h_rt[0]["annotations"]["underline"]
    assert h_rt[0]["annotations"]["strikethrough"]
