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
            "result.tcode": getattr(job, "tcode", None),
            "result.pcode": getattr(job, "pcode", None),
            "result.ticket_count": getattr(job, "ticket_count", None),
            "result.event_id": getattr(job, "event_id", None) or "151991",
            "result.shop": getattr(job, "shop", None) or "CV0",
            "result.custref": job.custref,
            "result.payment_url": job.payment_url,
            "result.amount_cents": job.amount_cents,
            "result.deadline_at": deadline_iso,
            # Keep seat-hold deadline on the order so pay worker / reaper can see it.
            "worker.lease_until": deadline_iso,
            "worker.worker_id": None,
            "worker.lease_token": None,
            "last_error.code": None,
            "last_error.message": None,
            "last_error.stage": None,
            "last_error.retryable": None,
        },
    )


def claim_locked_for_pay(
    store: Any,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = LEASE_SECONDS,
    order_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Atomically claim one locked order for payment: locked → paying.
    If order_id is set, only try that order (queue-driven).
    """
    moment = now or datetime.now(timezone.utc)
    now_iso = to_iso(moment)
    if order_id:
        rows = store.find(ORDERS_COLLECTION, {"order_id": order_id}, limit=1)
        candidates = rows
    else:
        candidates = store.find(
            ORDERS_COLLECTION,
            {
                "status": "locked",
                "result.payment_url": {"$exists": True, "$ne": None},
                "result.deadline_at": {"$gt": now_iso},
            },
            limit=20,
            sort={"result.deadline_at": 1, "updated_at": 1},
        )
    for cand in candidates:
        if (cand.get("status") or "") != "locked":
            continue
        result = cand.get("result") or {}
        if not result.get("payment_url") or not result.get("custref"):
            continue
        deadline = result.get("deadline_at")
        if deadline and deadline <= now_iso:
            continue
        token = secrets.token_hex(16)
        lease_until = moment + timedelta(seconds=lease_seconds)
        if deadline:
            try:
                from datetime import datetime as _dt

                dl = _dt.fromisoformat(str(deadline).replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if dl > lease_until:
                    lease_until = dl
            except Exception:
                pass
        n = store.update(
            ORDERS_COLLECTION,
            {
                "order_id": cand["order_id"],
                "status": "locked",
                "version": cand.get("version"),
            },
            {
                "status": "paying",
                "version": int(cand.get("version") or 1) + 1,
                "worker.worker_id": worker_id,
                "worker.lease_token": token,
                "worker.lease_until": to_iso(lease_until),
                "worker.heartbeat_at": now_iso,
                "updated_at": now_iso,
                "last_error.code": None,
                "last_error.message": None,
            },
        )
        if n == 1:
            return get_order(store, cand["order_id"])
    return None


def write_vcc_ids(
    store: Any,
    order_id: str,
    *,
    client_request_id: str = "",
    application_id: str = "",
    order_id_vcc: str = "",
    card_id: str = "",
) -> None:
    """Persist non-sensitive VCC ids on the order so retries reuse the same card."""
    fields: dict[str, Any] = {}
    if client_request_id:
        fields["result.vcc_client_request_id"] = client_request_id
    if application_id:
        fields["result.vcc_application_id"] = application_id
    if order_id_vcc:
        fields["result.vcc_order_id"] = order_id_vcc
    if card_id:
        fields["result.vcc_card_id"] = card_id
    if fields:
        patch_order(store, order_id, fields)


def write_paid(
    store: Any,
    order_id: str,
    *,
    purchase_id: str = "",
    vcc_order_id: str = "",
    final_url: str = "",
    payment_method: str = "",
) -> None:
    patch_order(
        store,
        order_id,
        {
            "status": "paid",
            "paid_at": iso_now(),
            "result.purchase_id": purchase_id or None,
            "result.vcc_order_id": vcc_order_id or None,
            "result.final_url": final_url or None,
            "result.payment_method": payment_method or None,
            "worker.worker_id": None,
            "worker.lease_token": None,
            "last_error.code": None,
            "last_error.message": None,
        },
    )


def write_pay_failed(
    store: Any,
    order: dict[str, Any],
    *,
    code: str,
    message: str,
    retryable: bool = False,
) -> dict[str, Any]:
    """
    Pay failed: do not bounce to queued (seat already held).
    retryable + still inside deadline → back to locked for another pay attempt;
    otherwise → manual_review.
    """
    now = datetime.now(timezone.utc)
    now_iso = to_iso(now)
    deadline = (order.get("result") or {}).get("deadline_at") or ""
    still_open = bool(deadline and deadline > now_iso)
    if retryable and still_open:
        status = "locked"
        fields = {
            "status": status,
            "worker.worker_id": None,
            "worker.lease_token": None,
            "worker.lease_until": deadline,
            "last_error.code": code,
            "last_error.stage": "pay",
            "last_error.message": (message or "")[:500],
            "last_error.retryable": True,
            "last_error.at": now_iso,
        }
    else:
        status = "manual_review"
        fields = {
            "status": status,
            "last_error.code": code,
            "last_error.stage": "pay",
            "last_error.message": (message or "")[:500],
            "last_error.retryable": False,
            "last_error.at": now_iso,
        }
    patch_order(store, order["order_id"], fields)
    return get_order(store, order["order_id"]) or order


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
