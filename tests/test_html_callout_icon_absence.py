from .fixtures import *  # noqa


async def test_html_callout_icon_absence():
    html = '<div class="callout">Callout text</div>'
    blocks = await nu.html_to_blocks(html)
    assert blocks, "Expected at least one block"
    callouts = [b for b in blocks if b.get("type") == "callout"]
    assert len(callouts) == 1, f"Expected exactly 1 callout block, got {len(callouts)}"
    callout = callouts[0].get("callout")
    assert isinstance(callout, dict), "callout payload should be a dict"
    # The callout should NOT contain an icon field when absent
    assert "icon" not in callout, "callout.icon should be omitted when no icon is present"
    rt = callout.get("rich_text")
    assert isinstance(rt, list) and rt and rt[0].get("plain_text") == "Callout text"
