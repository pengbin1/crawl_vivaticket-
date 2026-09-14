#!/usr/bin/env python3
"""Probe payment chain stages without submitting card (for flow validation)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from payer.http_pay import open_axerve_from_payment_url
from payer.secure_waf import bootstrap_secure_session
from shared.config import load_config
from shared.log import get_logger

logger = get_logger("pay_probe")


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe Vivaticket→Axerve payment chain")
    ap.add_argument("payment_url")
    ap.add_argument("--config", default=str(ROOT / "config.local.yaml"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    qs = parse_qs(urlparse(args.payment_url).query)
    custref = (qs.get("custref") or [""])[0]
    shop = (qs.get("shop") or ["CV0"])[0]
    if not custref:
        raise SystemExit("payment_url 缺少 custref")

    print("=== Stage 0: Drission open formtr (WAF + expiry check) ===")
    page = None
    try:
        session, page = bootstrap_secure_session(
            args.payment_url,
            headless=cfg.headless,
            proxy=cfg.proxy,
        )
        html = page.html or ""
        has_card_radio = "pmcreditcard" in html
        print(f"  OK  secure page loaded, pmcreditcard={has_card_radio}, len={len(html)}")
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return 2
    finally:
        if page:
            try:
                page.quit()
            except Exception:
                pass

    print("\n=== Stage 1–4: HTTP formtr → doauth → Sella → Axerve (with cookies) ===")
    try:
        ax = open_axerve_from_payment_url(
            payment_url=args.payment_url,
            custref=custref,
            shop=shop,
            user_agent=session.headers.get("User-Agent", ""),
            timeout=60.0,
            session=session,
        )
        print(f"  OK  flow_id={ax.flow_id}")
        print(f"  OK  axerve_url={ax.axerve_url}")
    except TypeError:
        # backward compat if session kw not wired yet
        print("  SKIP  http_pay session injection not available")
        return 1
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return 2

    print("\n=== Stage 5: Axerve fulfillFlow (pure HTTP submit) ===")
    print("  TODO  POST /orchestra/checkout/api/flows/{flowId}/fulfill 尚未抓包")
    print("        当前填卡提交走 payer/browser_pay.py (Drission)")
    print("\n结论: Vivaticket→Axerve 网关链可测；最终扣款需 Drission 或补抓 fulfill API。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
