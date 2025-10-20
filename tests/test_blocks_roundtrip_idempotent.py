import json
import os
from typing import Any, List

from .fixtures import *  # noqa


def _rt_texts(block: dict) -> List[str]:
    """Return list of plain_text strings for a block's rich_text (empty list if none)."""
    if not isinstance(block, dict):
        return []
    t = block.get("type")
    section = block.get(t) if isinstance(t, str) else None
    if not isinstance(section, dict):
        return []
    rt = section.get("rich_text")
    out: List[str] = []
    if isinstance(rt, list):
        for r in rt:
            if isinstance(r, dict):
                pt = r.get("plain_text")
                if isinstance(pt, str):
                    out.append(pt)
    return out

def clean(obj: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove keys with None values from a dictionary."""
    if not isinstance(obj, dict):
        return obj
    return {
        k: c
        for k, v in obj.items()
        if (c := clean(v)) and (k != 'color' or v != 'default')
    }


def verify(o, r, idx):
    o_type = o.get("type")
    r_type = r.get("type")
    assert o_type == r_type, f"Block type mismatch at index {idx}: {o_type} != {r_type}"
    
    o_texts = o.get(o_type, {}).get('rich_text', [])
    r_texts = r.get(r_type, {}).get('rich_text', [])
    for (o_t, r_t) in zip(o_texts, r_texts):
        o_t_c = clean(o_t)
        r_t_c = clean(r_t)
        assert o_t_c.get('text') == r_t_c.get('text'), (
            f"Rich text mismatch at index {idx}: {o_t_c} != {r_t_c}"
        )
        assert o_t_c.get('annotations') == r_t_c.get('annotations'), (
            f"Rich text annotations mismatch at index {idx}: {o_t_c} != {r_t_c}"
        )
    
    o_children = o.get(o_type, {}).get('children', [])
    r_children = r.get(r_type, {}).get('children', [])
    assert len(o_children) == len(r_children), (
        f"Children count mismatch at index {idx}: {len(o_children)} != {len(r_children)}"
    )
    for c_idx, (o_child, r_child) in enumerate(zip(o_children, r_children)):
        verify(o_child, r_child, f"{idx}.{c_idx}")


async def test_blocks_roundtrip_idempotent():
    """Round-trip block->HTML->blocks should preserve supported block sequence & text.

    This intentionally fails with current implementation for known lossy cases
    (e.g. empty paragraphs, code formatting nuances) to guide improvements.
    """
    data_path = os.path.join(os.path.dirname(__file__), "data", "format-blocks.json")
    with open(data_path, "r", encoding="utf-8") as f:
        original_blocks = json.load(f)

    html = await nu.blocks_to_html(original_blocks)
    regenerated = await nu.html_to_blocks(html)

    # Pairwise compare types and concatenated text content
    for idx, (o, r) in enumerate(zip(original_blocks, regenerated)):
        verify(o, r, str(idx))
        
    # Assert same number of supported blocks (expected to fail initially due to losses)
    assert len(original_blocks) == len(regenerated), (
        f"Supported block count changed: original={len(original_blocks)} regenerated={len(regenerated)}"
    )
