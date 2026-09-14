from __future__ import annotations

from pathlib import Path

from shared.config import AppConfig, PayQueueConfig
from shared.log import cenacolo_home
from worker.pay_queue import PayQueue


def resolve_queue_dir(cfg: PayQueueConfig | AppConfig) -> Path:
    pq = cfg.pay_queue if isinstance(cfg, AppConfig) else cfg
    if pq.queue_dir:
        return Path(pq.queue_dir).expanduser().resolve()
    return cenacolo_home() / "var" / "pay_queue"


def make_pay_queue(cfg: AppConfig | PayQueueConfig) -> PayQueue:
    return PayQueue(resolve_queue_dir(cfg))
