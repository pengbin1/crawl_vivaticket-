from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from worker.mongo_api import ACCOUNTS_COLLECTION, ORDERS_COLLECTION, RUNS_COLLECTION
from worker.timeutil import iso_now, to_iso

CLAIMABLE = ("queued", "retry_wait", "waiting_inventory")
LEASE_SECONDS = 300


def seed_order(
    store: Any,
    *,
    passengers: list[dict[str, str]],
    target_dates: list[str] | None = None,
    use_any_available: bool = False,
    ticket_count: int | None = None,
    priority: int = 50,
    next_attempt_at: str | None = None,
    not_before: str | None = None,
    expires_at: str | None = None,
    max_attempts: int = 10,
    order_id: str | None = None,
    order_no: str | None = None,
    user_id: str = "manual",
    contact: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    now = iso_now()
    oid = order_id or uuid4().hex
    count = ticket_count if ticket_count is not None else len(passengers)
    later = to_iso(datetime.now(timezone.utc) + timedelta(days=30))
    doc: dict[str, Any] = {
        "order_id": oid,
        "order_no": order_no or f"CEN{datetime.now(timezone.utc).strftime('%Y%m%d')}{oid[:6].upper()}",
        "idempotency_key": idempotency_key or oid,
        "user_id": user_id,
        "contact": contact or {"name": "", "email": "", "phone": ""},
        "passengers": passengers,
        "request": {
            "target_dates": list(target_dates or []),
            "use_any_available": use_any_available,
            "ticket_count": count,
            "priority": priority,
            "not_before": not_before or now,
            "expires_at": expires_at or later,
        },
        "priority": priority,
        "not_before": not_before or now,
        "expires_at": expires_at or later,
        "payment_profile_id": None,
        "status": "queued",
        "version": 1,
        "attempts": 0,
        "max_attempts": max_attempts,
        "next_attempt_at": next_attempt_at or now,
        "worker": {
            "worker_id": None,
            "lease_token": None,
            "lease_until": None,
            "heartbeat_at": None,
        },
        "account": {"email": None, "lease_token": None},
        "result": {
            "job_id": None,
            "date": None,
            "time": None,
            "custref": None,
            "payment_url": None,
            "amount_cents": None,
            "payment_method": None,
            "final_url": None,
            "deadline_at": None,
        },
        "last_error": {
            "code": None,
            "stage": None,
            "message": None,
            "retryable": None,
            "at": None,
        },
        "created_at": now,
        "updated_at": now,
        "paid_at": None,
        "cancelled_at": None,
    }
    store.insert(ORDERS_COLLECTION, doc)
    return doc


def get_order(store: Any, order_id: str) -> dict[str, Any] | None:
    rows = store.find(ORDERS_COLLECTION, {"order_id": order_id}, limit=1)
    return rows[0] if rows else None


def claim_order(
    store: Any,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = LEASE_SECONDS,
) -> dict[str, Any] | None:
    moment = now or datetime.now(timezone.utc)
    now_iso = to_iso(moment)
    candidates = store.find(
        ORDERS_COLLECTION,
        {
            "status": {"$in": list(CLAIMABLE)},
            "next_attempt_at": {"$lte": now_iso},
            "not_before": {"$lte": now_iso},
            "expires_at": {"$gt": now_iso},
        },
        limit=20,
        sort={"priority": -1, "created_at": 1},
    )
    for cand in candidates:
        token = secrets.token_hex(16)
        n = store.update(
            ORDERS_COLLECTION,
            {
                "order_id": cand["order_id"],
                "status": cand["status"],
                "version": cand["version"],
            },
            {
                "status": "processing",
                "version": int(cand.get("version") or 1) + 1,
                "worker.worker_id": worker_id,
                "worker.lease_token": token,
                "worker.lease_until": to_iso(moment + timedelta(seconds=lease_seconds)),
                "worker.heartbeat_at": now_iso,
                "updated_at": now_iso,
            },
        )
        if n == 1:
            return get_order(store, cand["order_id"])
    return None


def patch_order(store: Any, order_id: str, fields: dict[str, Any]) -> int:
    fields = {**fields, "updated_at": iso_now()}
    return store.update(ORDERS_COLLECTION, {"order_id": order_id}, fields)


def bind_account(store: Any, order_id: str, email: str, lease_token: str | None) -> None:
    patch_order(
        store,
        order_id,
        {"account.email": email, "account.lease_token": lease_token},
    )


def write_locked(store: Any, order_id: str, job: Any) -> None:
    deadline = job.deadline_at
    deadline_iso = to_iso(deadline) if isinstance(deadline, datetime) else str(deadline)
    patch_order(
        store,
        order_id,
        {
            "status": "locked",
            "result.job_id": job.job_id,
            "result.date": job.date,
            "result.time": job.time,
            "result.custref": job.custref,
            "result.payment_url": job.payment_url,
            "result.amount_cents": job.amount_cents,
            "result.deadline_at": deadline_iso,
            "worker.lease_until": deadline_iso,
            "last_error.code": None,
            "last_error.message": None,
        },
    )


def finish_attempt(
    store: Any,
    order: dict[str, Any],
    *,
    status: str,
    code: str | None = None,
    message: str | None = None,
    stage: str | None = None,
    retryable: bool | None = None,
    increment_attempts: bool = False,
    next_in_seconds: int | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    attempts = int(order.get("attempts") or 0)
    if increment_attempts:
        attempts += 1
        max_attempts = int(order.get("max_attempts") or 10)
        if attempts >= max_attempts and status == "retry_wait":
            status = "failed"
            retryable = False
    fields: dict[str, Any] = {
        "status": status,
        "attempts": attempts,
        "last_error.code": code,
        "last_error.stage": stage,
        "last_error.message": (message or "")[:500] or None,
        "last_error.retryable": retryable,
        "last_error.at": to_iso(now) if code or message else None,
    }
    if next_in_seconds is not None:
        fields["next_attempt_at"] = to_iso(now + timedelta(seconds=next_in_seconds))
    patch_order(store, order["order_id"], fields)
    return get_order(store, order["order_id"]) or order


def insert_run(
    store: Any,
    *,
    order: dict[str, Any],
    worker_id: str,
    account_email: str | None,
    stage: str,
    status: str,
) -> str:
    run_id = uuid4().hex
    store.insert(
        RUNS_COLLECTION,
        {
            "run_id": run_id,
            "order_id": order.get("order_id"),
            "order_no": order.get("order_no"),
            "attempt_no": int(order.get("attempts") or 0) + 1,
            "worker_id": worker_id,
            "account_email": account_email,
            "started_at": iso_now(),
            "finished_at": None,
            "stage": stage,
            "status": status,
            "retryable": None,
            "error_code": None,
            "error_message": None,
        },
    )
    return run_id


def finish_run(
    store: Any,
    run_id: str,
    *,
    stage: str,
    status: str,
    retryable: bool | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    store.update(
        RUNS_COLLECTION,
        {"run_id": run_id},
        {
            "finished_at": iso_now(),
            "stage": stage,
            "status": status,
            "retryable": retryable,
            "error_code": error_code,
            "error_message": (error_message or "")[:500] if error_message else None,
        },
    )


def renew_lease(
    store: Any,
    order_id: str,
    worker_id: str,
    lease_token: str,
    *,
    extra_seconds: int = LEASE_SECONDS,
) -> None:
    now = datetime.now(timezone.utc)
    store.update(
        ORDERS_COLLECTION,
        {
            "order_id": order_id,
            "worker.worker_id": worker_id,
            "worker.lease_token": lease_token,
        },
        {
            "worker.lease_until": to_iso(now + timedelta(seconds=extra_seconds)),
            "worker.heartbeat_at": to_iso(now),
        },
    )
    order = get_order(store, order_id)
    email = ((order or {}).get("account") or {}).get("email")
    if email:
        store.update(
            ACCOUNTS_COLLECTION,
            {"email": email, "lease.order_id": order_id},
            {"lease.lease_until": to_iso(now + timedelta(seconds=extra_seconds))},
        )
