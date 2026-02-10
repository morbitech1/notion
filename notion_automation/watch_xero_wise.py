"""Xero → Wise sync watcher.

Polls Xero for unpaid bills (Invoices where Type=ACCPAY) and creates Wise
transfers to pay them.

Reference rules:
- Uses the Xero bill `Reference` field for the Wise transfer reference.
- Falls back to `InvoiceNumber` when Reference is blank.

Payment details:
- Fetches the Xero Contact and uses `BankAccountDetails`.
- Extracts an IBAN from the string; if none found, the bill is skipped.

Idempotency:
- Persists processed Xero InvoiceIDs to a local JSON file.

Env vars:
- XERO_ACCESS_TOKEN, XERO_TENANT_ID, (optional) XERO_BASE_URL, XERO_BILLS_WHERE
- WISE_API_TOKEN, WISE_PROFILE_ID, (optional) WISE_BASE_URL
- (optional) XERO_WISE_STATE_PATH, XERO_WISE_DRY_RUN
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .wise_async import WiseClient
from .xero_async import XeroClient

logger = logging.getLogger(__name__)


DEFAULT_BILLS_WHERE = 'Type=="ACCPAY"&&Status=="AUTHORISED"&&AmountDue>0'


_IBAN_RE = re.compile(r"\b([A-Z]{2}[0-9]{2}[A-Z0-9]{11,30})\b")


def extract_iban(bank_account_details: str) -> Optional[str]:
    """Extract an IBAN from a Xero Contact.BankAccountDetails string."""
    if not bank_account_details:
        return None
    text = bank_account_details.strip().upper().replace(" ", "")
    m = _IBAN_RE.search(text)
    if not m:
        return None
    return m.group(1)


def select_wise_reference(bill: Dict[str, Any]) -> str:
    """Select the Wise payment reference from a Xero bill."""
    ref = bill.get("Reference")
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    inv = bill.get("InvoiceNumber")
    if isinstance(inv, str) and inv.strip():
        return inv.strip()
    return "Xero bill"


@dataclass
class XeroWiseConfig:
    xero_access_token: str
    xero_tenant_id: str
    xero_base_url: str
    xero_bills_where: str

    wise_api_token: str
    wise_profile_id: int
    wise_base_url: str

    state_path: Path
    dry_run: bool


def _env(name: str, default: Optional[str] = None) -> str:
    if default is None:
        return os.environ[name]
    return os.getenv(name, default)


def load_config() -> Optional[XeroWiseConfig]:
    """Load config from environment.

    Returns None when required vars are missing so that running multiple
    watchers in one command remains graceful.
    """
    try:
        xero_access_token = _env("XERO_ACCESS_TOKEN")
        xero_tenant_id = _env("XERO_TENANT_ID")
        wise_api_token = _env("WISE_API_TOKEN")
        wise_profile_id_raw = _env("WISE_PROFILE_ID")
    except KeyError:
        logger.info("Xero→Wise watcher disabled (missing XERO_* / WISE_* env vars)")
        return None

    try:
        wise_profile_id = int(wise_profile_id_raw)
    except ValueError:
        raise ValueError("WISE_PROFILE_ID must be an integer")

    xero_base_url = _env("XERO_BASE_URL", "https://api.xero.com/api.xro/2.0")
    wise_base_url = _env("WISE_BASE_URL", "https://api.transferwise.com")
    bills_where = _env("XERO_BILLS_WHERE", DEFAULT_BILLS_WHERE)

    state_path_str = _env("XERO_WISE_STATE_PATH", "rendered/xero-wise-state.json")
    dry_run = _env("XERO_WISE_DRY_RUN", "0") != "0"

    return XeroWiseConfig(
        xero_access_token=xero_access_token,
        xero_tenant_id=xero_tenant_id,
        xero_base_url=xero_base_url,
        xero_bills_where=bills_where,
        wise_api_token=wise_api_token,
        wise_profile_id=wise_profile_id,
        wise_base_url=wise_base_url,
        state_path=Path(state_path_str),
        dry_run=dry_run,
    )


def _load_state(path: Path) -> Set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except Exception:
        logger.warning("Failed reading state file %s; starting fresh", path, exc_info=True)
        return set()
    ids = data.get("processed_invoice_ids") if isinstance(data, dict) else None
    if isinstance(ids, list):
        return {str(x) for x in ids if x}
    return set()


def _save_state(path: Path, processed_ids: Set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"processed_invoice_ids": sorted(processed_ids)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def _process_bill(
    *,
    bill: Dict[str, Any],
    xero: XeroClient,
    wise: WiseClient,
    processed_ids: Set[str],
    dry_run: bool,
) -> bool:
    invoice_id = bill.get("InvoiceID")
    if not isinstance(invoice_id, str) or not invoice_id:
        return False
    if invoice_id in processed_ids:
        return False

    amount_due = bill.get("AmountDue")
    currency = bill.get("CurrencyCode")
    if not isinstance(amount_due, (int, float)) or amount_due <= 0:
        return False
    if not isinstance(currency, str) or not currency.strip():
        logger.warning("Skipping bill InvoiceID=%s: missing CurrencyCode", invoice_id)
        return False

    contact = bill.get("Contact") if isinstance(bill.get("Contact"), dict) else {}
    contact_id = contact.get("ContactID") if isinstance(contact, dict) else None
    contact_name = contact.get("Name") if isinstance(contact, dict) else None

    if isinstance(contact_id, str) and contact_id:
        contact_full = await xero.get_contact(contact_id)
        if isinstance(contact_full, dict):
            contact = contact_full
            if not contact_name and isinstance(contact.get("Name"), str):
                contact_name = contact.get("Name")

    bank_details = contact.get("BankAccountDetails") if isinstance(contact, dict) else None
    if not isinstance(bank_details, str) or not bank_details.strip():
        logger.warning("Skipping bill InvoiceID=%s: contact missing BankAccountDetails", invoice_id)
        return False

    iban = extract_iban(bank_details)
    if not iban:
        logger.warning(
            "Skipping bill InvoiceID=%s: no IBAN found in BankAccountDetails=%r",
            invoice_id,
            bank_details,
        )
        return False

    reference = select_wise_reference(bill)
    account_holder_name = (contact_name or "Xero contact").strip()

    logger.info(
        "Preparing Wise transfer InvoiceID=%s amount_due=%.2f %s ref=%r to=%r iban=%s",
        invoice_id,
        float(amount_due),
        currency,
        reference,
        account_holder_name,
        iban,
    )

    if dry_run:
        processed_ids.add(invoice_id)
        return True

    # Wise: create recipient -> quote -> transfer -> fund
    recipient_id = await wise.create_recipient_iban(
        account_holder_name=account_holder_name,
        iban=iban,
        currency=currency,
    )
    quote_uuid = await wise.create_quote(currency=currency, target_amount=float(amount_due))
    transfer_id = await wise.create_transfer(
        quote_uuid=quote_uuid,
        target_account_id=recipient_id,
        customer_transaction_id=invoice_id,
        reference=reference,
    )
    await wise.fund_transfer(transfer_id=transfer_id)

    processed_ids.add(invoice_id)
    logger.info("Created + funded Wise transfer transfer_id=%s for InvoiceID=%s", transfer_id, invoice_id)
    return True


async def watch_xero_wise_async(
    *,
    poll_interval: int,
    stop_event: asyncio.Event,
) -> None:
    cfg = load_config()
    if cfg is None:
        return

    xero = XeroClient(cfg.xero_access_token, cfg.xero_tenant_id, base_url=cfg.xero_base_url)
    wise = WiseClient(cfg.wise_api_token, cfg.wise_profile_id, base_url=cfg.wise_base_url)

    processed_ids = _load_state(cfg.state_path)
    logger.info(
        "Xero→Wise watcher started dry_run=%s where=%r state=%s processed=%d",
        cfg.dry_run,
        cfg.xero_bills_where,
        str(cfg.state_path),
        len(processed_ids),
    )

    while True:
        if stop_event.is_set():
            break
        try:
            bills = await xero.iter_bills(where=cfg.xero_bills_where)
            created = 0
            for bill in bills:
                if stop_event.is_set():
                    break
                try:
                    ok = await _process_bill(
                        bill=bill,
                        xero=xero,
                        wise=wise,
                        processed_ids=processed_ids,
                        dry_run=cfg.dry_run,
                    )
                    if ok:
                        created += 1
                        _save_state(cfg.state_path, processed_ids)
                except Exception:
                    invoice_id = bill.get("InvoiceID")
                    logger.exception("Failed processing bill InvoiceID=%r", invoice_id)
            if created:
                logger.info("Xero→Wise cycle complete created=%d", created)
        except Exception:
            logger.exception("Xero→Wise poll cycle failed")

        await asyncio.sleep(poll_interval)

    _save_state(cfg.state_path, processed_ids)
    logger.info("Xero→Wise watcher stopped")


async def run_xero_wise_watcher(args: argparse.Namespace, stop_event: asyncio.Event) -> None:
    await watch_xero_wise_async(poll_interval=args.poll_interval, stop_event=stop_event)
