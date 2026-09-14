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


def process_once(
    store: Any,
    base_cfg: AppConfig,
    worker_id: str,
    *,
    locker_fn: LockerFn | None = None,
    now: Any = None,
    no_inventory_wait_seconds: int = 60,
) -> dict[str, Any] | None:
    """
    Claim one order, reserve one account, lock a seat, write payment_url.
    Does NOT pay — hand off to run_pay_worker (status=locked).
    """
    order = claim_order(store, worker_id, now=now)
    if not order:
        return None

    account = reserve_account(store, order["order_id"], worker_id, now=now)
    if not account:
        logger.warning("[锁座Worker] 无可用账号 order=%s", order.get("order_no"))
        notify(
            f"[cenacolo_worker] 无账号 order={order.get('order_no')}",
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
                logger.warning("[锁座Worker] 心跳失败: %s", exc)

    beat = threading.Thread(target=_heartbeat, daemon=True)
    beat.start()
    page = None
    try:
        cfg = build_app_config(base_cfg, order, account)
        logger.info(
            "[锁座Worker] 开始锁座 order=%s account=%s dates=%s",
            order.get("order_no"),
            cfg.email,
            cfg.target_dates,
        )
        job, _session, page = locker(cfg)
        write_locked(store, order["order_id"], job)
        # Free account for other lock attempts; payment uses payment_url only.
        release_account(store, account["email"], to_status="ready")
        finish_run(store, run_id, stage="lock", status="success")

        # Dual insurance: durable queue + HTTP wake (pay worker also scans Mongo).
        try:
            from worker.pay_notify import notify_pay_wake
            from worker.pay_queue import PayQueueJob
            from worker.queue_util import make_pay_queue

            if base_cfg.pay_queue.enabled:
                q = make_pay_queue(base_cfg)
                deadline_iso = (
                    job.deadline_at.isoformat()
                    if hasattr(job.deadline_at, "isoformat")
                    else str(job.deadline_at)
                )
                q.enqueue(
                    PayQueueJob(
                        order_id=str(order["order_id"]),
                        order_no=str(order.get("order_no") or ""),
                        custref=job.custref,
                        payment_url=job.payment_url,
                        deadline_at=deadline_iso,
                    )
                )
                notify_pay_wake(
                    base_cfg.pay_queue.wake_url,
                    order_id=str(order["order_id"]),
                    order_no=str(order.get("order_no") or ""),
                    custref=job.custref,
                )
        except Exception as exc:
            logger.warning(
                "[锁座Worker] 入队/通知失败（Mongo locked 仍可被支付扫到）: %s",
                exc,
            )

        notify(
            f"[cenacolo_worker] 已锁座 {order.get('order_no')} "
            f"{job.date} {job.time} custref={job.custref}\n"
            f"已入支付队列\n{job.payment_url}",
            base_cfg.feishu_webhook,
            base_cfg.feishu_secret,
        )
        logger.info(
            "[锁座Worker] 成功 order=%s custref=%s → locked + 支付队列",
            order.get("order_no"),
            job.custref,
        )
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
            logger.info("[锁座Worker] 无票 order=%s", order.get("order_no"))
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
        logger.exception("[锁座Worker] 失败 order=%s: %s", order.get("order_no"), exc)
        notify(
            f"[cenacolo_worker] 锁座失败 {order.get('order_no')}: {exc}",
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
