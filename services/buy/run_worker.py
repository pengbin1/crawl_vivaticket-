from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config import load_config
from shared.log import get_logger, setup_logging
from worker.mongo_api import HttpStore
from worker.orders import seed_order
from worker.pipeline import process_once
from worker.reaper import reap_expired

logger = get_logger("buy.worker")


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def _parse_passengers(raw: str) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"passenger must be first:last, got {part!r}")
        first, last = part.split(":", 1)
        people.append({"first_name": first.strip(), "last_name": last.strip()})
    if not people:
        raise SystemExit("need at least one passenger")
    return people


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cenacolo reservation worker (phase 1: lock only, no auto-pay)"
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config.local.yaml"),
        help="runtime yaml (captcha/browser/notify); order/account come from Mongo",
    )
    parser.add_argument("--once", action="store_true", help="process at most one order then exit")
    parser.add_argument("--reap", action="store_true", help="run lease reaper once then exit")
    parser.add_argument(
        "--seed-order",
        action="store_true",
        help="insert a queued test order into cenacolo_orders then exit",
    )
    parser.add_argument(
        "--passengers",
        default="Peng:Bin",
        help="comma-separated first:last for --seed-order",
    )
    parser.add_argument(
        "--dates",
        default="",
        help="comma-separated YYYY-MM-DD for --seed-order; empty + --any = first available",
    )
    parser.add_argument(
        "--any",
        action="store_true",
        help="with --seed-order, use_any_available=true",
    )
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between polls when busy")
    parser.add_argument("--idle-poll", type=float, default=5.0, help="seconds when queue empty")
    args = parser.parse_args(argv)

    setup_logging(service="buy-worker")
    store = HttpStore()
    if args.seed_order:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
        doc = seed_order(
            store,
            passengers=_parse_passengers(args.passengers),
            target_dates=dates,
            use_any_available=args.any or not dates,
        )
        logger.info(
            "[worker] seeded order_no=%s order_id=%s dates=%s any=%s",
            doc["order_no"],
            doc["order_id"],
            dates,
            doc["request"]["use_any_available"],
        )
        return 0

    if args.reap:
        n = reap_expired(store)
        logger.info("[worker] reaped %s expired leases", n)
        return 0

    cfg = load_config(args.config)
    worker_id = _worker_id()
    logger.info("[worker] start worker_id=%s lock-only phase1", worker_id)

    idle = 0
    while True:
        try:
            reap_expired(store)
            outcome = process_once(store, cfg, worker_id)
        except Exception as exc:
            logger.exception("[worker] loop error: %s", exc)
            outcome = None
        if args.once:
            return 0 if outcome is None or outcome.get("status") in {"locked", "waiting_inventory", "retry_wait"} else 1
        if outcome:
            idle = 0
            time.sleep(max(args.poll, 0.5))
        else:
            idle += 1
            time.sleep(args.idle_poll if idle > 1 else args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
