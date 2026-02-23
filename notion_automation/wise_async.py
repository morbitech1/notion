"""Minimal async Wise client for the Xero → Wise sync watcher.

This is a small, purpose-built wrapper around the Wise public API.
The watcher currently supports:
- Create recipient account from IBAN
- Create a quote (balance pay-in)
- Create a transfer
- Fund the transfer from Wise balance

API surface is intentionally narrow; extend as needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from .http_async import request_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WiseClient:
    api_token: str
    profile_id: int
    base_url: str = "https://api.transferwise.com"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def create_recipient_iban(
        self,
        *,
        account_holder_name: str,
        iban: str,
        currency: str,
    ) -> int:
        """Create a Wise recipient account (IBAN). Returns recipient account id."""
        url = f"{self.base_url.rstrip('/')}/v1/accounts"
        payload: Dict[str, Any] = {
            "profile": self.profile_id,
            "accountHolderName": account_holder_name,
            "currency": currency,
            "type": "iban",
            "details": {"iban": iban},
        }
        data = await request_json("POST", url, headers=self.headers, json=payload)
        rid = data.get("id")
        if not isinstance(rid, int):
            raise RuntimeError(f"Wise recipient account create returned unexpected id: {rid!r}")
        return rid

    async def create_quote(
        self,
        *,
        currency: str,
        target_amount: float,
        pay_out: str = "BANK_TRANSFER",
        preferred_pay_in: str = "BALANCE",
    ) -> str:
        """Create a quote and return quote UUID."""
        url = f"{self.base_url.rstrip('/')}/v3/profiles/{self.profile_id}/quotes"
        payload: Dict[str, Any] = {
            "sourceCurrency": currency,
            "targetCurrency": currency,
            "targetAmount": float(target_amount),
            "payOut": pay_out,
            "preferredPayIn": preferred_pay_in,
        }
        data = await request_json("POST", url, headers=self.headers, json=payload)
        qid = data.get("id") or data.get("uuid")
        if not isinstance(qid, str) or not qid:
            raise RuntimeError(f"Wise quote create returned unexpected id: {qid!r}")
        return qid

    async def create_transfer(
        self,
        *,
        quote_uuid: str,
        target_account_id: int,
        customer_transaction_id: str,
        reference: str,
    ) -> int:
        """Create a transfer. Returns transfer id."""
        url = f"{self.base_url.rstrip('/')}/v1/transfers"
        payload: Dict[str, Any] = {
            "targetAccount": target_account_id,
            "quoteUuid": quote_uuid,
            "customerTransactionId": customer_transaction_id,
            "details": {"reference": reference},
        }
        data = await request_json("POST", url, headers=self.headers, json=payload)
        tid = data.get("id")
        if not isinstance(tid, int):
            raise RuntimeError(f"Wise transfer create returned unexpected id: {tid!r}")
        return tid

    async def fund_transfer(self, *, transfer_id: int) -> Dict[str, Any]:
        """Fund a transfer from Wise balance."""
        url = f"{self.base_url.rstrip('/')}/v3/profiles/{self.profile_id}/transfers/{transfer_id}/payments"
        payload = {"type": "BALANCE"}
        return await request_json("POST", url, headers=self.headers, json=payload)

    async def get_transfer(self, *, transfer_id: int) -> Dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/v1/transfers/{transfer_id}"
        return await request_json("GET", url, headers=self.headers)
