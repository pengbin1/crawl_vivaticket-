"""Durable local pay queue (file-based). Mongo locked status remains source of truth."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.log import get_logger

logger = get_logger(__name__)


@dataclass
class PayQueueJob:
    order_id: str
    order_no: str = ""
    custref: str = ""
    payment_url: str = ""
    deadline_at: str = ""
    enqueued_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "order_no": self.order_no,
            "custref": self.custref,
            "payment_url": self.payment_url,
            "deadline_at": self.deadline_at,
            "enqueued_at": self.enqueued_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PayQueueJob":
        return cls(
            order_id=str(data.get("order_id") or ""),
            order_no=str(data.get("order_no") or ""),
            custref=str(data.get("custref") or ""),
            payment_url=str(data.get("payment_url") or ""),
            deadline_at=str(data.get("deadline_at") or ""),
            enqueued_at=str(data.get("enqueued_at") or ""),
        )


class PayQueue:
    """
    pending/ → processing/ → done/ | dead/
    Idempotent enqueue by order_id filename.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.processing = self.root / "processing"
        self.done = self.root / "done"
        self.dead = self.root / "dead"
        for d in (self.pending, self.processing, self.done, self.dead):
            d.mkdir(parents=True, exist_ok=True)

    def _path(self, folder: Path, order_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in order_id)[:120]
        return folder / f"{safe}.json"

    def enqueue(self, job: PayQueueJob) -> Path:
        if not job.order_id:
            raise ValueError("order_id required")
        if not job.enqueued_at:
            job.enqueued_at = datetime.now(timezone.utc).isoformat()
        # Already processing / pending → refresh payload but keep place.
        for folder in (self.processing, self.pending):
            path = self._path(folder, job.order_id)
            if path.exists():
                path.write_text(
                    json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(
                    "[支付队列] 已存在，刷新任务 order_id=%s dir=%s",
                    job.order_id,
                    folder.name,
                )
                return path
        # Remove stale done/dead so a re-lock can pay again (rare).
        for folder in (self.done, self.dead):
            stale = self._path(folder, job.order_id)
            if stale.exists():
                stale.unlink(missing_ok=True)
        dest = self._path(self.pending, job.order_id)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, dest)
        logger.info(
            "[支付队列] 入队 order_id=%s order_no=%s custref=%s",
            job.order_id,
            job.order_no,
            job.custref,
        )
        return dest

    def claim_next(self, worker_id: str) -> PayQueueJob | None:
        files = sorted(self.pending.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for src in files:
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                job = PayQueueJob.from_dict(data)
                if not job.order_id:
                    src.rename(self._path(self.dead, src.stem))
                    continue
                dest = self._path(self.processing, job.order_id)
                # Atomic claim: rename pending → processing
                os.rename(src, dest)
                meta = job.to_dict()
                meta["claimed_by"] = worker_id
                meta["claimed_at"] = datetime.now(timezone.utc).isoformat()
                dest.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                logger.info(
                    "[支付队列] 出队领取 order_id=%s worker=%s",
                    job.order_id,
                    worker_id,
                )
                return job
            except FileNotFoundError:
                continue
            except OSError:
                continue
            except Exception as exc:
                logger.warning("[支付队列] 领取失败 file=%s err=%s", src.name, exc)
        return None

    def ack(self, order_id: str) -> None:
        src = self._path(self.processing, order_id)
        if not src.exists():
            src = self._path(self.pending, order_id)
        if not src.exists():
            return
        dest = self._path(self.done, order_id)
        try:
            os.replace(src, dest)
            logger.info("[支付队列] 完成并移除 processing→done order_id=%s", order_id)
        except OSError as exc:
            logger.warning("[支付队列] ack 失败 order_id=%s err=%s", order_id, exc)

    def requeue(self, order_id: str) -> None:
        src = self._path(self.processing, order_id)
        if not src.exists():
            return
        dest = self._path(self.pending, order_id)
        try:
            os.replace(src, dest)
            logger.info("[支付队列] 失败回队 order_id=%s", order_id)
        except OSError as exc:
            logger.warning("[支付队列] requeue 失败 order_id=%s err=%s", order_id, exc)

    def dead_letter(self, order_id: str, reason: str = "") -> None:
        src = self._path(self.processing, order_id)
        if not src.exists():
            src = self._path(self.pending, order_id)
        if not src.exists():
            return
        dest = self._path(self.dead, order_id)
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            data["dead_reason"] = reason[:500]
            data["dead_at"] = datetime.now(timezone.utc).isoformat()
            dest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            src.unlink(missing_ok=True)
            logger.warning(
                "[支付队列] 进死信 order_id=%s reason=%s", order_id, reason[:120]
            )
        except OSError as exc:
            logger.warning("[支付队列] dead 失败 order_id=%s err=%s", order_id, exc)

    def pending_count(self) -> int:
        return len(list(self.pending.glob("*.json")))
