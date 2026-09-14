from __future__ import annotations

from dataclasses import replace
from typing import Any

from shared.config import AppConfig
from shared.models import Passenger


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
