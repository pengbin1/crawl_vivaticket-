from __future__ import annotations

import threading
from typing import Any, Callable

from shared.config import AppConfig
from shared.log import get_logger
from shared.notify import notify
from worker.accounts import release_account, reserve_account
from worker.adapter import build_app_config
from worker.errors import is_no_inventory
from worker.orders import (
    bind_account,
    claim_order,
    finish_attempt,
    finish_run,
    get_order,
    insert_run,
    renew_lease,
    write_locked,
)

logger = get_logger("cenacolo_worker")

LockerFn = Callable[[AppConfig], tuple[Any, Any, Any]]
PayFn = Callable[..., Any]


def process_once(
    store: Any,
    base_cfg: AppConfig,
    worker_id: str,
    *,
    locker_fn: LockerFn | None = None,
    pay_fn: PayFn | None = None,
    now: Any = None,
    no_inventory_wait_seconds: int = 60,
    auto_pay: bool = False,
) -> dict[str, Any] | None:
    """
    Claim one order, reserve one account, lock a seat, write payment_url.
    Phase 1 does not pay unless auto_pay=True (still unused by default).
    """
    del pay_fn  # phase-1: lock only; kept so tests can assert it is not called
    order = claim_order(store, worker_id, now=now)
    if not order:
        return None

    account = reserve_account(store, order["order_id"], worker_id, now=now)
    if not account:
        logger.warning("[worker] no ready account for %s", order.get("order_no"))
        notify(
            f"[cenacolo_worker] NO_ACCOUNT order={order.get('order_no')}",
            base_cfg.feishu_webhook,
            base_cfg.feishu_secret,
        )
        return finish_attempt(
            store,
            order,
            status="retry_wait",
            code="NO_ACCOUNT",
            message="no ready vivaticket account",
            stage="claim",
            retryable=True,
            increment_attempts=True,
            next_in_seconds=60,
        )

    bind_account(
        store,
        order["order_id"],
        account["email"],
        (account.get("lease") or {}).get("lease_token"),
    )
    run_id = insert_run(
        store,
        order=order,
        worker_id=worker_id,
        account_email=account.get("email"),
        stage="lock",
        status="running",
    )

    locker = locker_fn or _default_locker
    stop = threading.Event()
    token = (order.get("worker") or {}).get("lease_token") or ""

    def _heartbeat() -> None:
        while not stop.wait(30):
            try:
                renew_lease(store, order["order_id"], worker_id, token)
            except Exception as exc:
                logger.warning("[worker] heartbeat failed: %s", exc)

    beat = threading.Thread(target=_heartbeat, daemon=True)
    beat.start()
    page = None
    try:
        cfg = build_app_config(base_cfg, order, account)
        logger.info(
            "[worker] lock order=%s account=%s dates=%s",
            order.get("order_no"),
            cfg.email,
            cfg.target_dates,
        )
        job, _session, page = locker(cfg)
        write_locked(store, order["order_id"], job)
        finish_run(store, run_id, stage="lock", status="success")
        if auto_pay:
            raise RuntimeError("auto_pay is not enabled in phase 1")
        notify(
            f"[cenacolo_worker] LOCKED {order.get('order_no')} "
            f"{job.date} {job.time} custref={job.custref}\n{job.payment_url}",
            base_cfg.feishu_webhook,
            base_cfg.feishu_secret,
        )
        logger.info("[worker] locked order=%s custref=%s", order.get("order_no"), job.custref)
        return get_order(store, order["order_id"])
    except Exception as exc:
        if is_no_inventory(exc):
            finish_run(
                store,
                run_id,
                stage="inventory",
                status="failed",
                retryable=True,
                error_code="NO_INVENTORY",
                error_message=str(exc),
            )
            release_account(store, account["email"], to_status="ready")
            updated = finish_attempt(
                store,
                order,
                status="waiting_inventory",
                code="NO_INVENTORY",
                message=str(exc),
                stage="inventory",
                retryable=True,
                increment_attempts=False,
                next_in_seconds=no_inventory_wait_seconds,
            )
            logger.info("[worker] no inventory order=%s", order.get("order_no"))
            return updated
        finish_run(
            store,
            run_id,
            stage="lock",
            status="failed",
            retryable=True,
            error_code="LOCK_FAILED",
            error_message=str(exc),
        )
        release_account(store, account["email"], to_status="ready")
        logger.exception("[worker] lock failed order=%s: %s", order.get("order_no"), exc)
        notify(
            f"[cenacolo_worker] ERROR {order.get('order_no')}: {exc}",
            base_cfg.feishu_webhook,
            base_cfg.feishu_secret,
        )
        return finish_attempt(
            store,
            order,
            status="retry_wait",
            code="LOCK_FAILED",
            message=str(exc),
            stage="lock",
            retryable=True,
            increment_attempts=True,
            next_in_seconds=30,
        )
    finally:
        stop.set()
        if page is not None:
            try:
                page.quit()
            except Exception:
                pass


def _default_locker(cfg: AppConfig) -> tuple[Any, Any, Any]:
    from locker.pipeline import run_locker

    return run_locker(cfg)
