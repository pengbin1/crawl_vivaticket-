#!/usr/bin/env python3
"""Pay against an existing formtr URL (debug / resume within 20min window)."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from payer.orchestrator import pay_job
from shared.config import load_config
from shared.log import get_logger
from shared.models import PaymentJob, utc_now

logger = get_logger("pay_only")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payment_url", help="formtr.php?custref=VIVATK...&shop=CV0")
    ap.add_argument("--config", default=str(ROOT / "config.local.yaml"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    qs = parse_qs(urlparse(args.payment_url).query)
    custref = (qs.get("custref") or [""])[0]
    if not custref:
        m = re.search(r"VIVATK\d+", args.payment_url)
        custref = m.group(0) if m else ""
    if not custref:
        raise SystemExit("payment_url 缺少 custref")

    locked_at = utc_now()
    job = PaymentJob(
        payment_url=args.payment_url,
        custref=custref,
        account_email=cfg.email,
        date="",
        time="",
        tcode="",
        pcode="",
        ticket_count=cfg.ticket_count,
        amount_cents=0,
        passengers=list(cfg.passengers),
        user_agent="",
        locked_at=locked_at,
        deadline_at=locked_at + timedelta(seconds=cfg.lock_ttl_seconds),
        shop=(qs.get("shop") or ["CV0"])[0],
    )
    result = pay_job(job, cfg)
    logger.info("ok=%s method=%s msg=%s", result.ok, result.method, result.message)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
