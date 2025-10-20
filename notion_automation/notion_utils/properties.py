from typing import Any


def extract_rich_text_plain(prop: Any) -> str:
    """Return concatenated plain text from a Notion property supporting rich_text/title/rollup.

    Handles shapes:
      - {"rich_text": [ ... ]}
      - {"title": [ ... ]}
      - {"rollup": {"type": "array", "array": [ {rich_text|title|...}, ... ]}}
    Falls back gracefully to empty string.
    """
    if pt := prop.get('plain_text'):
        return pt
    if ptype := prop.get("type"):
        data = prop.get(ptype)
        if isinstance(data, list):
            return ','.join(s for r in data if (s := extract_rich_text_plain(r)))
        if isinstance(data, dict):
            return extract_rich_text_plain(data)
        if isinstance(data, str):
            return data
    return ""


def extract_emails(prop: Any) -> list[str]:
    """Extract unique email-like strings from a Notion property.

    Supports 'people', 'multi_select', and 'rich_text' property shapes. Returns
    lower‑cased unique entries preserving first occurrence order. Comma‑separated
    lists are split into individual addresses.
    """
    emails: list[str] = []
    if isinstance(prop, dict):
        direct_email = prop.get("email")
        if isinstance(direct_email, str):
            emails.append(direct_email)
        people = prop.get("people")
        if isinstance(people, list):
            for p in people:
                if isinstance(p, dict):
                    e = p.get("person", {}).get("email") if isinstance(p.get("person"), dict) else None
                    if isinstance(e, str):
                        emails.append(e)
        multi = prop.get("multi_select")
        if isinstance(multi, list):
            for m in multi:
                if isinstance(m, dict):
                    name = m.get("name")
                    if isinstance(name, str) and "@" in name:
                        emails.append(name)
        rt = prop.get("rich_text")
        if isinstance(rt, list):
            for r in rt:
                if isinstance(r, dict):
                    pt = r.get("plain_text")
                    if isinstance(pt, str) and "@" in pt:
                        emails.append(pt)
    seen: set[str] = set()
    unique: list[str] = []
    for e in emails:
        for part in e.split(","):
            part = part.strip().lower()
            if part not in seen:
                seen.add(part)
                unique.append(part)
    return unique
