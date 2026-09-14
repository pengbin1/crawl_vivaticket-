from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.config import CardConfig


@dataclass
class VccApplication:
    client_request_id: str
    application_id: str
    order_id: str
    provider_request_id: str = ""
    status: str = ""
    card_id: str | None = None
    cardholder_name: str = ""
    cardholder_email: str = ""
    create_body: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    env: str = ""


@dataclass
class VccSensitiveCard:
    """In-memory only. Never log number/cvv."""

    card_id: str
    number: str
    cvv: str
    expiry_month: str
    expiry_year: str
    card_status: str
    card_bin: str = ""

    def last4(self) -> str:
        digits = "".join(c for c in self.number if c.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""


@dataclass
class VccPayContext:
    application: VccApplication
    sensitive: VccSensitiveCard
    card: CardConfig
    payment_id: str
    started_at: str
    cost_amount: str
    cost_currency: str
    report_payload: dict[str, Any] | None = None
    purchase_id: str = ""
