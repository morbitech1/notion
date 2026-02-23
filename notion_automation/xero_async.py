"""Minimal async Xero client for the Xero → Wise sync watcher.

This module intentionally supports only the endpoints needed for paying bills:
- List unpaid bills (Invoices where Type=ACCPAY)
- Fetch Contact details (for BankAccountDetails)

Auth: bearer token + xero-tenant-id header. Token refresh is handled externally.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .http_async import request_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class XeroClient:
    access_token: str
    tenant_id: str
    base_url: str = "https://api.xero.com/api.xro/2.0"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
        }

    async def list_bills(self, *, where: str, page: int = 1) -> List[Dict[str, Any]]:
        """Return a page of bills (Invoices) matching the Xero where filter."""
        url = f"{self.base_url.rstrip('/')}/Invoices"
        params = {"where": where, "page": str(page)}
        data = await request_json("GET", url, headers=self.headers, params=params)
        invoices = data.get("Invoices")
        if not isinstance(invoices, list):
            return []
        return [i for i in invoices if isinstance(i, dict)]

    async def iter_bills(self, *, where: str, max_pages: int = 50) -> List[Dict[str, Any]]:
        """Fetch bills across pages until empty or max_pages reached."""
        out: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            items = await self.list_bills(where=where, page=page)
            if not items:
                break
            out.extend(items)
        return out

    async def get_contact(self, contact_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a contact by ID."""
        if not contact_id:
            return None
        url = f"{self.base_url.rstrip('/')}/Contacts/{contact_id}"
        data = await request_json("GET", url, headers=self.headers)
        contacts = data.get("Contacts")
        if isinstance(contacts, list) and contacts:
            first = contacts[0]
            if isinstance(first, dict):
                return first
        # Some Xero responses may include Contact at top-level.
        contact = data.get("Contact")
        return contact if isinstance(contact, dict) else None
