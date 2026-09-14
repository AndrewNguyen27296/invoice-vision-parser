"""
The canned provider. Free, offline, deterministic -- and the reason the whole
V1 UI could be built and demoed without an API key or a cent of spend.

The response is shaped to exercise every downstream path: decimal quantities,
a percentage in a description, a stated tax rate, and arithmetic that audits
clean.
"""

from __future__ import annotations

import json

from .base import ProviderReply

MOCK_INVOICE = {
    "vendor_name": "Nordfrakt Logistik AB",
    "vendor_tax_id": "SE556677889901",
    "invoice_no": "NF-2026-04417",
    "invoice_date": "2026-08-21",
    "due_date": "2026-09-20",
    "currency": "SEK",
    "line_items": [
        {"description": "Road freight Gothenburg -> Malmo, 4 pallets",
         "quantity": 4, "unit_price": 185.00, "line_total": 740.00},
        {"description": "Fuel surcharge 12%", "quantity": 1,
         "unit_price": 88.80, "line_total": 88.80},
        {"description": "Waiting time, 1.5 h", "quantity": 1.5,
         "unit_price": 420.00, "line_total": 630.00},
        {"description": "Pallet exchange fee", "quantity": 4,
         "unit_price": 22.80, "line_total": 91.20},
    ],
    "subtotal": 1550.00,
    "tax_rate": 25,
    "tax_amount": 387.50,
    "total_amount": 1937.50,
}


class MockProvider:
    name = "mock"
    free = True
    trains_on_data = False      # nothing leaves the process

    def describe(self) -> str:
        return "Mock (canned response) - no network, no key, no cost"

    def call(self, pages, cfg) -> ProviderReply:
        return ProviderReply(raw_text=json.dumps(MOCK_INVOICE), model="mock")
