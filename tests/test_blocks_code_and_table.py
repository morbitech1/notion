from .fixtures import *  # noqa


async def test_blocks_to_html_code_block():
    blocks = [
        {"type": "code", "code": {"rich_text": [{"plain_text": "print('hi')"}], "language": "python"}},
    ]
    html = await nu.blocks_to_html(blocks)
    assert '<pre><code' in html and 'print(&#x27;hi&#x27;)' in html
    assert 'language-python' in html


async def test_blocks_to_html_table_basic():
    blocks = [
        {"type": "table", "table": {"has_column_header": True, "has_row_header": False, "children": [
            {"type": "table_row", "table_row": {"cells": [[{"plain_text": "Col1"}], [{"plain_text": "Col2"}]]}},
            {"type": "table_row", "table_row": {"cells": [[{"plain_text": "A"}], [{"plain_text": "B"}]]}},
        ]}},
    ]
    html = await nu.blocks_to_html(blocks)
    assert '<table>' in html and '</table>' in html
    assert '<thead>' in html and '<th>Col1</th>' in html
    assert '<tbody>' in html and '<td>B</td>' in html
