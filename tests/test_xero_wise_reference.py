from notion_automation.watch_xero_wise import select_wise_reference


def test_select_wise_reference_prefers_reference() -> None:
    bill = {"Reference": "SUP-INV-123", "InvoiceNumber": "BILL-9"}
    assert select_wise_reference(bill) == "SUP-INV-123"


def test_select_wise_reference_falls_back_invoice_number() -> None:
    bill = {"Reference": "  ", "InvoiceNumber": "BILL-9"}
    assert select_wise_reference(bill) == "BILL-9"


def test_select_wise_reference_default() -> None:
    bill = {}
    assert select_wise_reference(bill) == "Xero bill"
