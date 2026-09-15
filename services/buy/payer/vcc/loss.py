"""Asset loss confirmation per crawler loss API docs.

Only after procurement created AVAILABLE assets; for tickets that cannot be
sold and will not be supplier-refunded (e.g. debug / test unsellable tickets).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from payer.vcc.client import VccClient
from payer.vcc.errors import VccError, VccManualRequired
from payer.vcc.store import VccStore
from shared.log import get_logger

logger = get_logger(__name__)


def build_loss_request_id(custref: str, asset_id: str, attempt: int = 1) -> str:
    """Stable per asset loss fact. Persist before POST; never rotate on retry."""
    safe_c = "".join(c for c in (custref or "UNKNOWN") if c.isalnum() or c in "-_")
    safe_a = "".join(c for c in (asset_id or "") if c.isalnum() or c in "-_")[-40:]
    return f"CENACOLO-LOSS-{safe_c}-{safe_a}-{int(attempt)}"


def build_loss_order_id(*, business_order_no: str = "", custref: str = "") -> str:
    """
    Finance-facing orderId. Prefer cenacolo order_no; fallback custref.
    Do NOT default to VCC card order_id unless finance confirms.
    """
    for candidate in (business_order_no, custref):
        text = (candidate or "").strip()
        if text:
            return text[:100]
    raise ValueError("loss order_id requires business_order_no or custref")


def confirm_asset_loss(
    client: VccClient,
    store: VccStore,
    *,
    asset_id: str,
    request_id: str,
    order_id: str,
    reason: str,
    confirmed_at: str | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    GET asset → must be AVAILABLE → persist snapshot → POST /losses.
    Idempotent on request_id + full body.
    """
    if not asset_id:
        raise VccError("asset_id required for loss", code="config")
    if not request_id or not order_id or not reason:
        raise VccError("request_id/order_id/reason required", code="config")

    client.ensure_env()
    asset_payload = client.get_asset(asset_id)
    asset = asset_payload.get("data") or {}
    status = str(asset.get("status") or "").upper()
    logger.info(
        "[VCC][损耗] 查资产 asset_id=%s status=%s",
        asset_id,
        status or "-",
    )
    if status == "LOSS_CONFIRMED":
        # Already lost — try to return from local snapshot if any.
        existing = store.load_loss_payload(request_id)
        logger.warning(
            "[VCC][损耗] 资产已是 LOSS_CONFIRMED，不再换号重报 asset_id=%s",
            asset_id,
        )
        return {
            "asset_id": asset_id,
            "status": "LOSS_CONFIRMED",
            "already_confirmed": True,
            "request_id": request_id,
            "local_snapshot": existing,
        }
    if status != "AVAILABLE":
        raise VccManualRequired(
            f"asset status={status} 不是 AVAILABLE，不能报损",
            code="state_conflict",
            trace_id=str(asset_payload.get("trace_id") or ""),
        )

    body = {
        "request_id": request_id,
        "order_id": order_id,
        "reason": reason[:500],
        "confirmed_at": confirmed_at
        or datetime.now(timezone.utc).astimezone().isoformat(),
    }
    # First snapshot wins for retries.
    store.save_loss_payload(request_id, {**body, "asset_id": asset_id})
    body = {
        k: v
        for k, v in (store.load_loss_payload(request_id) or {}).items()
        if k in {"request_id", "order_id", "reason", "confirmed_at"}
    }
    # Ensure asset_id path matches persisted record
    persisted = store.load_loss_payload(request_id) or {}
    if persisted.get("asset_id") and persisted["asset_id"] != asset_id:
        raise VccManualRequired(
            "loss snapshot asset_id mismatch; do not retarget",
            code="idempotency_conflict",
        )

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp, status_code = client.report_loss(asset_id, body)
        except VccError as exc:
            last_exc = exc
            if exc.http_status == 409 or exc.code in {
                "idempotency_conflict",
                "state_conflict",
            }:
                raise VccManualRequired(
                    str(exc),
                    code=exc.code or "state_conflict",
                    http_status=exc.http_status,
                    trace_id=exc.trace_id,
                ) from exc
            if attempt < max_attempts and (
                exc.retryable or (exc.http_status or 0) in {500, 503}
            ):
                time.sleep(1)
                continue
            raise

        if status_code == 409:
            err = resp.get("error") or {}
            raise VccManualRequired(
                str(err.get("message") or "loss conflict"),
                code=str(err.get("code") or "state_conflict"),
                http_status=409,
                trace_id=str(resp.get("trace_id") or ""),
            )
        if status_code >= 500 and attempt < max_attempts:
            time.sleep(1)
            continue
        if status_code >= 400:
            err = resp.get("error") or {}
            raise VccError(
                str(err.get("message") or f"loss HTTP {status_code}"),
                code=str(err.get("code") or ""),
                http_status=status_code,
                trace_id=str(resp.get("trace_id") or ""),
            )

        data = resp.get("data") or {}
        if status_code in {200, 201} and data.get("adjustment_id"):
            logger.info(
                "[VCC][损耗] 已确认 asset_id=%s adjustment_id=%s "
                "finance_bundle_id=%s",
                data.get("asset_id") or asset_id,
                data.get("adjustment_id"),
                data.get("finance_bundle_id"),
            )
            store.save_loss_result(request_id, data)
            return data
        raise VccManualRequired(
            "loss 2xx without adjustment_id; treat as uncertain",
            code="loss_uncertain",
            http_status=status_code,
            trace_id=str(resp.get("trace_id") or ""),
        )

    raise VccManualRequired(
        f"loss retries exhausted: {last_exc}",
        code="loss_exhausted",
    )


def confirm_losses_for_assets(
    client: VccClient,
    store: VccStore,
    *,
    asset_ids: list[str],
    custref: str,
    business_order_no: str = "",
    reason: str,
) -> list[dict[str, Any]]:
    """Confirm loss for each procurement asset_id (one POST per asset)."""
    results: list[dict[str, Any]] = []
    order_id = build_loss_order_id(
        business_order_no=business_order_no, custref=custref
    )
    for asset_id in asset_ids:
        aid = str(asset_id or "").strip()
        if not aid:
            continue
        request_id = build_loss_request_id(custref, aid, attempt=1)
        logger.info(
            "[VCC][损耗] 开始报损 custref=%s asset_id=%s "
            "request_id=%s order_id=%s reason=%s",
            custref,
            aid,
            request_id,
            order_id,
            reason,
        )
        results.append(
            confirm_asset_loss(
                client,
                store,
                asset_id=aid,
                request_id=request_id,
                order_id=order_id,
                reason=reason,
            )
        )
    return results
