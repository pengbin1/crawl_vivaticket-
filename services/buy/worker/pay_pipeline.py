from __future__ import annotations

import threading
from typing import Any, Callable

from shared.config import AppConfig
from shared.log import get_logger
from shared.notify import notify
from worker.adapter import payment_job_from_order
from worker.orders import (
    claim_locked_for_pay,
    finish_run,
    get_order,
    insert_run,
    renew_lease,
    write_paid,
    write_pay_failed,
)
from worker.queue_util import make_pay_queue

logger = get_logger("cenacolo_pay_worker")

PayFn = Callable[..., Any]


def process_pay_once(
    store: Any,
    base_cfg: AppConfig,
    worker_id: str,
    *,
    pay_fn: PayFn | None = None,
    now: Any = None,
    prefer_queue: bool = True,
) -> dict[str, Any] | None:
    """
    Prefer durable pay queue, then Mongo locked scan (dual insurance).
    On success: ack queue. On retryable fail: requeue. Else dead-letter.
    """
    queue = make_pay_queue(base_cfg) if base_cfg.pay_queue.enabled else None
    queue_job = None
    order = None

    if prefer_queue and queue is not None:
        queue_job = queue.claim_next(worker_id)
        if queue_job:
            order = claim_locked_for_pay(
                store, worker_id, now=now, order_id=queue_job.order_id
            )
            if not order:
                cur = get_order(store, queue_job.order_id)
                status = (cur or {}).get("status")
                logger.info(
                    "[支付Worker] 队列任务无法 claim order=%s mongo_status=%s，"
                    "继续扫 Mongo",
                    queue_job.order_id,
                    status,
                )
                if status in {"paid", "manual_review", "cancelled", "expired"}:
                    queue.ack(queue_job.order_id)
                elif status == "paying":
                    # Another pay worker holds it.
                    queue.requeue(queue_job.order_id)
                else:
                    # locked missing / unknown — requeue and fall through to mongo scan
                    queue.requeue(queue_job.order_id)
                queue_job = None
                order = None

    if order is None:
        order = claim_locked_for_pay(store, worker_id, now=now)
        if not order:
            return None
        logger.info("[支付Worker] Mongo 兜底领取 locked order=%s", order.get("order_no"))

    order_no = order.get("order_no")
    oid = str(order["order_id"])
    logger.info(
        "[支付Worker] 开始支付 order=%s custref=%s deadline=%s source=%s",
        order_no,
        (order.get("result") or {}).get("custref"),
        (order.get("result") or {}).get("deadline_at"),
        "queue" if queue_job else "mongo",
    )

    run_id = insert_run(
        store,
        order=order,
        worker_id=worker_id,
        account_email=(order.get("account") or {}).get("email"),
        stage="pay",
        status="running",
    )
    stop = threading.Event()
    token = (order.get("worker") or {}).get("lease_token") or ""

    def _heartbeat() -> None:
        while not stop.wait(30):
            try:
                renew_lease(store, oid, worker_id, token)
            except Exception as exc:
                logger.warning("[支付Worker] 心跳失败: %s", exc)

    beat = threading.Thread(target=_heartbeat, daemon=True)
    beat.start()

    payer = pay_fn
    if payer is None:
        from payer.orchestrator import pay_job as _pay_job

        def payer(job, cfg, page=None):  # type: ignore[misc]
            return _pay_job(job, cfg, page=page, order_id=oid, order_store=store)

    try:
        job = payment_job_from_order(order)
        result = payer(job, base_cfg, page=None)
        if getattr(result, "ok", False):
            write_paid(
                store,
                oid,
                purchase_id=str(getattr(result, "purchase_id", "") or ""),
                vcc_order_id=str(getattr(result, "vcc_order_id", "") or ""),
                final_url=str(getattr(result, "final_url", "") or ""),
                payment_method=str(getattr(result, "method", "") or ""),
            )
            finish_run(store, run_id, stage="pay", status="success")
            if queue is not None:
                queue.ack(oid)
            notify(
                f"[cenacolo_pay] 支付成功 {order_no}\n"
                f"custref={job.custref}\n"
                f"purchase_id={getattr(result, 'purchase_id', '')}\n"
                f"vcc_order_id={getattr(result, 'vcc_order_id', '')}",
                base_cfg.feishu_webhook,
                base_cfg.feishu_secret,
            )
            logger.info(
                "[支付Worker] 成功 order=%s purchase_id=%s（已出队）",
                order_no,
                getattr(result, "purchase_id", ""),
            )
            return get_order(store, oid)

        msg = str(getattr(result, "message", result) or "pay failed")
        retryable = str(getattr(result, "raw_hint", "") or "") in {
            "unverified",
            "network_error",
            "needs_browser",
        }
        updated = write_pay_failed(
            store,
            order,
            code="PAY_FAILED",
            message=msg,
            retryable=retryable,
        )
        finish_run(
            store,
            run_id,
            stage="pay",
            status="failed",
            retryable=retryable,
            error_code="PAY_FAILED",
            error_message=msg,
        )
        if queue is not None:
            if retryable and updated.get("status") == "locked":
                queue.requeue(oid)
            else:
                queue.dead_letter(oid, reason=msg)
        notify(
            f"[cenacolo_pay] 支付失败 {order_no} → {updated.get('status')}\n{msg}",
            base_cfg.feishu_webhook,
            base_cfg.feishu_secret,
        )
        logger.warning(
            "[支付Worker] 失败 order=%s status=%s msg=%s",
            order_no,
            updated.get("status"),
            msg,
        )
        return updated
    except Exception as exc:
        logger.exception("[支付Worker] 异常 order=%s: %s", order_no, exc)
        finish_run(
            store,
            run_id,
            stage="pay",
            status="failed",
            retryable=False,
            error_code="PAY_ERROR",
            error_message=str(exc),
        )
        updated = write_pay_failed(
            store,
            order,
            code="PAY_ERROR",
            message=str(exc),
            retryable=False,
        )
        if queue is not None:
            queue.dead_letter(oid, reason=str(exc))
        notify(
            f"[cenacolo_pay] 支付异常 {order_no}: {exc}",
            base_cfg.feishu_webhook,
            base_cfg.feishu_secret,
        )
        return updated
    finally:
        stop.set()
