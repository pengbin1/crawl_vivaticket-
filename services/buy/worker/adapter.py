from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from shared.config import AppConfig
from shared.models import Passenger, PaymentJob


def build_app_config(base: AppConfig, order: dict[str, Any], account: dict[str, Any]) -> AppConfig:
    request = order.get("request") or {}
    raw_passengers = order.get("passengers") or []
    passengers = [
        Passenger(
            first_name=str(p.get("first_name") or "").strip(),
            last_name=str(p.get("last_name") or "").strip(),
        )
        for p in raw_passengers
    ]
    if not passengers:
        raise ValueError(f"order {order.get('order_id')} has no passengers")
    ticket_count = int(request.get("ticket_count") or len(passengers) or 1)
    return replace(
        base,
        email=str(account.get("email") or ""),
        password=str(account.get("password") or ""),
        target_dates=list(request.get("target_dates") or []),
        use_any_available=bool(request.get("use_any_available", False)),
        ticket_count=ticket_count,
        passengers=passengers,
        poll_when_empty=False,
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def payment_job_from_order(order: dict[str, Any]) -> PaymentJob:
    """Rebuild PaymentJob from a locked/paying Mongo order for the pay worker."""
    result = order.get("result") or {}
    request = order.get("request") or {}
    payment_url = str(result.get("payment_url") or "")
    if not payment_url:
        raise ValueError(f"order {order.get('order_id')} missing payment_url")
    custref = str(result.get("custref") or "")
    if not custref:
        qs = parse_qs(urlparse(payment_url).query)
        custref = (qs.get("custref") or [""])[0]
    if not custref:
        raise ValueError(f"order {order.get('order_id')} missing custref")

    passengers = [
        Passenger(
            first_name=str(p.get("first_name") or "").strip(),
            last_name=str(p.get("last_name") or "").strip(),
        )
        for p in (order.get("passengers") or [])
    ]
    if not passengers:
        passengers = [Passenger("Guest", "User")]

    deadline = _parse_dt(result.get("deadline_at"))
    locked_at = _parse_dt(order.get("updated_at") or order.get("created_at"))
    shop = str(result.get("shop") or "")
    if not shop:
        shop = (parse_qs(urlparse(payment_url).query).get("shop") or ["CV0"])[0]

    return PaymentJob(
        payment_url=payment_url,
        custref=custref,
        account_email=str((order.get("account") or {}).get("email") or ""),
        date=str(result.get("date") or ""),
        time=str(result.get("time") or ""),
        tcode=str(result.get("tcode") or ""),
        pcode=str(result.get("pcode") or ""),
        ticket_count=int(
            result.get("ticket_count")
            or request.get("ticket_count")
            or len(passengers)
            or 1
        ),
        amount_cents=int(result.get("amount_cents") or 0),
        passengers=passengers,
        user_agent="",
        locked_at=locked_at,
        deadline_at=deadline,
        job_id=str(result.get("job_id") or ""),
        shop=shop or "CV0",
        event_id=str(result.get("event_id") or "151991"),
        vcc_client_request_id=str(result.get("vcc_client_request_id") or ""),
        vcc_application_id=str(result.get("vcc_application_id") or ""),
        vcc_order_id=str(result.get("vcc_order_id") or ""),
        vcc_card_id=str(result.get("vcc_card_id") or ""),
        business_order_no=str(order.get("order_no") or ""),
    )
