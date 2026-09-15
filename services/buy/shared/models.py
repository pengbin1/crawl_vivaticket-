from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Passenger:
    first_name: str
    last_name: str


@dataclass
class DayInventory:
    event_id: str
    date: str
    tcode: str
    pcode: str
    seats: str
    calendar_code: str = "0"


@dataclass
class TimeSlot:
    date: str
    ora: str
    seats: str
    tcode: str
    pcode: str


@dataclass
class PaymentJob:
    payment_url: str
    custref: str
    account_email: str
    date: str
    time: str
    tcode: str
    pcode: str
    ticket_count: int
    amount_cents: int
    passengers: list[Passenger]
    user_agent: str
    locked_at: datetime
    deadline_at: datetime
    job_id: str = field(default_factory=lambda: uuid4().hex)
    shop: str = "CV0"
    event_id: str = "151991"
    cookies: dict[str, str] = field(default_factory=dict)
    # Persisted VCC ids for retry without opening a new card.
    vcc_client_request_id: str = ""
    vcc_application_id: str = ""
    vcc_order_id: str = ""
    vcc_card_id: str = ""
    # Cenacolo order_no for finance loss order_id (not VCC card order_id).
    business_order_no: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["locked_at"] = self.locked_at.isoformat()
        data["deadline_at"] = self.deadline_at.isoformat()
        return data


@dataclass
class PayResult:
    ok: bool
    method: str  # http | browser | vcc | none
    message: str
    final_url: str = ""
    raw_hint: str = ""
    confirmed: bool = False
    vcc_order_id: str = ""
    purchase_id: str = ""
    asset_ids: list[str] = field(default_factory=list)
    loss_results: list[dict[str, Any]] = field(default_factory=list)
