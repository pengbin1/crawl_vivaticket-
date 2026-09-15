#!/usr/bin/env python3
"""Manually confirm inventory loss for unsellable / non-refundable tickets.

Docs: POST /api/v1/assets/{asset_id}/losses
Requires prior procurement report that returned asset_id (AVAILABLE).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payer.vcc.flow import make_client, make_store
from payer.vcc.loss import (
    build_loss_order_id,
    build_loss_request_id,
    confirm_asset_loss,
)
from shared.config import load_config
from shared.log import default_artifact_dir, get_logger, setup_logging

logger = get_logger("buy.report_loss")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VCC 资产损耗上报（测试票/不可售不退）")
    ap.add_argument("--config", default=str(ROOT / "config.local.yaml"))
    ap.add_argument("--asset-id", required=True, help="采购上报返回的 AST-...")
    ap.add_argument("--custref", required=True, help="用于生成稳定 request_id")
    ap.add_argument(
        "--order-no",
        default="",
        help="财务 order_id，默认用 custref；优先传 cenacolo order_no",
    )
    ap.add_argument(
        "--reason",
        default="",
        help="默认用 config payment.vcc.loss_reason",
    )
    ap.add_argument(
        "--request-id",
        default="",
        help="可选；默认 CENACOLO-LOSS-{custref}-{asset}-1，重试必须复用",
    )
    args = ap.parse_args(argv)

    setup_logging(service="buy-loss")
    cfg = load_config(args.config)
    client = make_client(cfg.vcc)
    store = make_store(cfg.vcc, default_artifact_dir())
    reason = (args.reason or cfg.vcc.loss_reason or "debug_unsellable_no_refund").strip()
    order_id = build_loss_order_id(
        business_order_no=args.order_no, custref=args.custref
    )
    request_id = (args.request_id or "").strip() or build_loss_request_id(
        args.custref, args.asset_id, attempt=1
    )

    logger.info(
        "[损耗脚本] asset_id=%s request_id=%s order_id=%s reason=%s env=%s",
        args.asset_id,
        request_id,
        order_id,
        reason,
        cfg.vcc.expected_env,
    )
    data = confirm_asset_loss(
        client,
        store,
        asset_id=args.asset_id,
        request_id=request_id,
        order_id=order_id,
        reason=reason,
    )
    logger.info("[损耗脚本] 结果 %s", data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
