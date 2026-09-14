from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from worker.mongo_api import ACCOUNTS_COLLECTION
from worker.timeutil import iso_now, to_iso

SITE = "cenacolovinciano"
LEASE_SECONDS = 300


def reserve_account(
    store: Any,
    order_id: str,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = LEASE_SECONDS,
    site: str = SITE,
) -> dict[str, Any] | None:
    moment = now or datetime.now(timezone.utc)
    candidates = store.find(
        ACCOUNTS_COLLECTION,
        {"site": site, "status": "ready"},
        limit=10,
        sort={"updated_at": 1},
    )
    for cand in candidates:
        email = cand.get("email")
        if not email:
            continue
        token = secrets.token_hex(16)
        n = store.update(
            ACCOUNTS_COLLECTION,
            {"email": email, "status": "ready"},
            {
                "status": "reserved",
                "lease.order_id": order_id,
                "lease.worker_id": worker_id,
                "lease.lease_token": token,
                "lease.lease_until": to_iso(moment + timedelta(seconds=lease_seconds)),
                "updated_at": to_iso(moment),
            },
        )
        if n == 1:
            rows = store.find(ACCOUNTS_COLLECTION, {"email": email}, limit=1)
            return rows[0] if rows else None
    return None


def release_account(store: Any, email: str, to_status: str = "ready") -> None:
    store.update(
        ACCOUNTS_COLLECTION,
        {"email": email},
        {
            "status": to_status,
            "lease.order_id": None,
            "lease.worker_id": None,
            "lease.lease_token": None,
            "lease.lease_until": None,
            "updated_at": iso_now(),
        },
    )


def mark_account(
    store: Any,
    email: str,
    status: str,
    *,
    used_order_id: str | None = None,
    review_reason: str | None = None,
) -> None:
    fields: dict[str, Any] = {"status": status, "updated_at": iso_now()}
    if used_order_id:
        fields["used_order_id"] = used_order_id
        fields["used_at"] = iso_now()
    if review_reason:
        fields["review_reason"] = review_reason
    if status in {"used", "review", "cooldown"}:
        # keep lease.order_id for audit; drop worker token
        fields["lease.worker_id"] = None
        fields["lease.lease_token"] = None
    store.update(ACCOUNTS_COLLECTION, {"email": email}, fields)
