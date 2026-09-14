#!/usr/bin/env python3
"""Pay worker: queue + wake API + Mongo locked fallback."""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config import load_config
from shared.log import get_logger, setup_logging
from worker.mongo_api import HttpStore
from worker.pay_notify import WakeServer
from worker.pay_pipeline import process_pay_once
from worker.queue_util import make_pay_queue
from worker.reaper import reap_expired

logger = get_logger("buy.pay_worker")


def _worker_id() -> str:
    return f"pay-{socket.gethostname()}-{os.getpid()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cenacolo pay worker（队列消费 + /wake 通知 + Mongo 兜底）"
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config.local.yaml"),
        help="runtime yaml；支付走 payment.vcc",
    )
    parser.add_argument("--once", action="store_true", help="最多处理一单后退出")
    parser.add_argument("--poll", type=float, default=1.0, help="有唤醒/有队列时间隔")
    parser.add_argument(
        "--idle-poll",
        type=float,
        default=None,
        help="空闲等待秒（默认用 pay_queue.mongo_fallback_seconds）",
    )
    args = parser.parse_args(argv)

    setup_logging(service="buy-pay-worker")
    cfg = load_config(args.config)
    store = HttpStore()
    worker_id = _worker_id()
    idle_poll = (
        float(args.idle_poll)
        if args.idle_poll is not None
        else float(cfg.pay_queue.mongo_fallback_seconds or 15)
    )

    logger.info(
        "[支付Worker] 启动 worker_id=%s VCC=%s 队列=%s wake=%s:%s 兜底扫库=%.0fs",
        worker_id,
        "开" if cfg.vcc.enabled else "关",
        "开" if cfg.pay_queue.enabled else "关",
        cfg.pay_queue.wake_listen_host,
        cfg.pay_queue.wake_listen_port,
        idle_poll,
    )
    if not cfg.vcc.enabled:
        logger.warning(
            "[支付Worker] payment.vcc.enabled=false：可消费队列，但不会开 VCC"
        )

    wake_event = threading.Event()
    wake_server = None
    if cfg.pay_queue.enabled:
        q = make_pay_queue(cfg)
        logger.info(
            "[支付Worker] 队列目录=%s pending≈%s",
            q.root,
            q.pending_count(),
        )
        try:
            wake_server = WakeServer(
                cfg.pay_queue.wake_listen_host,
                cfg.pay_queue.wake_listen_port,
                wake_event,
            )
            wake_server.start()
        except OSError as exc:
            logger.warning(
                "[支付Worker] wake 端口占用/失败，仅依赖队列+扫库: %s", exc
            )
            wake_server = None

    idle = 0
    try:
        while True:
            try:
                reap_expired(store)
                outcome = process_pay_once(store, cfg, worker_id)
            except Exception as exc:
                logger.exception("[支付Worker] 循环异常: %s", exc)
                outcome = None
            if args.once:
                if outcome is None:
                    return 0
                return (
                    0
                    if outcome.get("status") in {"paid", "locked", "manual_review"}
                    else 1
                )
            if outcome:
                idle = 0
                wake_event.clear()
                time.sleep(max(args.poll, 0.2))
                continue
            # Idle: wait for wake or mongo fallback interval.
            idle += 1
            wake_event.wait(timeout=idle_poll if idle > 1 else max(args.poll, 0.5))
            wake_event.clear()
    finally:
        if wake_server is not None:
            wake_server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
