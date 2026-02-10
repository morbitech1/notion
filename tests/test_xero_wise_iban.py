from notion_automation.watch_xero_wise import extract_iban


def test_extract_iban_basic() -> None:
    assert extract_iban("IBAN: GB82 WEST 1234 5698 7654 32") == "GB82WEST12345698765432"


def test_extract_iban_none() -> None:
    assert extract_iban("") is None
    assert extract_iban("no bank details") is None
