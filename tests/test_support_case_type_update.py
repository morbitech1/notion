from email.message import EmailMessage

import notion_automation.notion_utils.support_case as sc

from .fixtures import *  # noqa: F401,F403


def test_support_case_type_updated_when_found(monkeypatch):
    msg = EmailMessage()
    msg["From"] = "customer@example.com"
    msg["To"] = "engineering@company.domain"
    msg["Subject"] = "Type update"

    monkeypatch.setattr(sc.nuc, "NOTION_SUPPORT_CASES_DB_ID", "supportcasedb")
    monkeypatch.setattr(sc.nuc, "NOTION_CONTACTS_DB_ID", "")
    monkeypatch.setattr(sc.nuc, "ENGINEERING_ALIAS", "engineering@company.domain")
    monkeypatch.setattr(sc.nuc, "SUPPORT_ALIAS", "support@company.domain")
    monkeypatch.setattr(sc.nuc, "TRACKING_ALIAS", "notion@company.domain")

    async def async_title(*_a, **_k):
        return "Name"

    monkeypatch.setattr(sc.nua, "get_database_title_property", async_title)

    existing_page = {
        "id": "case1",
        "properties": {
            sc.nuc.PROP_SUPPORT_CASE_STATUS: {
                "type": "select",
                "select": {"name": sc.nuc.VAL_STATUS_OPEN},
            },
            sc.nuc.PROP_SUPPORT_CASE_TYPE: {
                "type": "multi_select",
                "multi_select": [{"name": sc.nuc.VAL_TYPE_SUPPORT}],
            },
        },
    }

    async def fake_find_case(*_a, **_k):
        return existing_page

    monkeypatch.setattr(sc, "find_support_case", fake_find_case)

    captured: dict[str, object] = {}

    async def fake_patch(page_id: str, props: dict):
        captured["page_id"] = page_id
        captured["props"] = props

    monkeypatch.setattr(sc.nua, "patch_page", fake_patch)

    page_id = run_async(sc.find_or_create_support_case(msg))
    assert page_id == "case1"

    assert captured["page_id"] == "case1"
    patch_props = captured["props"]
    assert isinstance(patch_props, dict)

    assert sc.nuc.PROP_SUPPORT_CASE_TYPE in patch_props
    type_patch = patch_props[sc.nuc.PROP_SUPPORT_CASE_TYPE]
    assert type_patch["multi_select"][0]["name"] == sc.nuc.VAL_TYPE_TECHNICAL
