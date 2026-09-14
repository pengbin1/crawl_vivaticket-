#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from locker.pipeline import run_locker
from payer.orchestrator import pay_job
from shared.config import load_config
from shared.log import get_logger
from shared.notify import dump_json, notify

logger = get_logger("cenacolo_buy")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cenacolo unattended buy + pay")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config.local.yaml"),
        help="path to config yaml",
    )
    parser.add_argument(
        "--lock-only",
        action="store_true",
        help="stop after lock (still saves payment_url); for debug only",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)

    notify(
        f"[cenacolo_buy] start account={cfg.email} prefer_browser={cfg.prefer_browser}",
        cfg.feishu_webhook,
        cfg.feishu_secret,
    )

    page = None
    try:
        job, session, page = run_locker(cfg)
        job_path = artifacts / f"job_{job.job_id}.json"
        dump_json(str(job_path), job.to_dict())
        logger.info("[main] locked → saved %s", job_path)
        notify(
            f"[cenacolo_buy] LOCKED {job.date} {job.time} custref={job.custref}\n{job.payment_url}",
            cfg.feishu_webhook,
            cfg.feishu_secret,
        )

        if args.lock_only:
            logger.warning("[main] --lock-only set; NOT paying (seat hold ~20min)")
            return 0

        # Pay immediately inside the 20-minute window.
        result = pay_job(job, cfg, page=None)
        out = {
            "ok": result.ok,
            "method": result.method,
            "message": result.message,
            "final_url": result.final_url,
            "job": job.to_dict(),
        }
        dump_json(str(artifacts / f"pay_{job.job_id}.json"), out)
        logger.info("[main] pay ok=%s method=%s msg=%s", result.ok, result.method, result.message)
        return 0 if result.ok else 2
    except Exception as exc:
        logger.exception("[main] failed: %s", exc)
        notify(f"[cenacolo_buy] ERROR: {exc}", cfg.feishu_webhook, cfg.feishu_secret)
        return 1
    finally:
        if page is not None:
            try:
                page.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
