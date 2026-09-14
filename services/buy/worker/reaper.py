from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worker.accounts import mark_account, release_account
from worker.mongo_api import ACCOUNTS_COLLECTION, ORDERS_COLLECTION
from worker.orders import patch_order
from worker.timeutil import iso_now, to_iso


def reap_expired(store: Any, now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    now_iso = to_iso(moment)
    recovered = 0

    for order in store.find(ORDERS_COLLECTION, {"status": "processing"}, limit=200):
        lease_until = (order.get("worker") or {}).get("lease_until")
        if not lease_until or lease_until >= now_iso:
            continue
        patch_order(
            store,
            order["order_id"],
            {
                "status": "retry_wait",
                "next_attempt_at": iso_now(),
                "worker.worker_id": None,
                "worker.lease_token": None,
                "worker.lease_until": None,
                "last_error.code": "LEASE_EXPIRED",
                "last_error.stage": "claim",
                "last_error.message": "processing lease expired",
                "last_error.retryable": True,
            },
        )
        email = (order.get("account") or {}).get("email")
        if email:
            _release_if_reserved(store, email, order["order_id"])
        recovered += 1

    for status in ("locked", "paying"):
        for order in store.find(ORDERS_COLLECTION, {"status": status}, limit=200):
            lease_until = (order.get("worker") or {}).get("lease_until")
            if not lease_until or lease_until >= now_iso:
                continue
            deadline = (order.get("result") or {}).get("deadline_at") or ""
            # Pay worker died but seat window still open → put back to locked for retry.
            if status == "paying" and deadline and deadline > now_iso:
                patch_order(
                    store,
                    order["order_id"],
                    {
                        "status": "locked",
                        "worker.worker_id": None,
                        "worker.lease_token": None,
                        "worker.lease_until": deadline,
                        "last_error.code": "PAY_LEASE_EXPIRED",
                        "last_error.stage": "pay",
                        "last_error.message": "paying lease expired; requeue locked for pay",
                        "last_error.retryable": True,
                    },
                )
                recovered += 1
                continue
            patch_order(
                store,
                order["order_id"],
                {
                    "status": "manual_review",
                    "last_error.code": "LEASE_EXPIRED",
                    "last_error.stage": status,
                    "last_error.message": f"{status} lease expired; do not auto-pay",
                    "last_error.retryable": False,
                },
            )
            email = (order.get("account") or {}).get("email")
            if email:
                mark_account(
                    store,
                    email,
                    "review",
                    review_reason=f"order {order.get('order_no')} {status} lease expired",
                )
            recovered += 1

    for acct in store.find(ACCOUNTS_COLLECTION, {"status": "reserved"}, limit=200):
        lease = acct.get("lease") or {}
        until = lease.get("lease_until")
        if not until or until >= now_iso:
            continue
        order_id = lease.get("order_id")
        order_status = None
        if order_id:
            rows = store.find(ORDERS_COLLECTION, {"order_id": order_id}, limit=1)
            order_status = rows[0].get("status") if rows else None
        if order_status in {"locked", "paying", "manual_review", "paid"}:
            mark_account(
                store,
                acct["email"],
                "review",
                review_reason=f"reserved lease expired while order={order_status}",
            )
        else:
            release_account(store, acct["email"], to_status="ready")
        recovered += 1

    return recovered


def _release_if_reserved(store: Any, email: str, order_id: str) -> None:
    rows = store.find(ACCOUNTS_COLLECTION, {"email": email}, limit=1)
    if not rows:
        return
    acct = rows[0]
    if acct.get("status") != "reserved":
        return
    lease_oid = (acct.get("lease") or {}).get("order_id")
    if lease_oid and lease_oid != order_id:
        return
    release_account(store, email, to_status="ready")
