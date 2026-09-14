from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from payer.vcc.client import VccClient
from payer.vcc.errors import VccError, VccManualRequired
from payer.vcc.store import VccStore
from shared.log import get_logger
from shared.models import PaymentJob

logger = get_logger(__name__)


def build_resource_key(
    *,
    event_id: str,
    date: str,
    time_slot: str,
    tcode: str,
    pcode: str,
) -> str | None:
    """Four sale dimensions; omit entirely if any is missing/ambiguous."""
    if not all([event_id, date, time_slot, tcode]):
        return None
    date_compact = date.replace("-", "")
    session = "".join(c for c in time_slot if c.isalnum())
    if not session:
        return None
    # pcode is useful but train profile uses TYPE=pl; include when present.
    parts = [
        f"CENACOLO_VIVATICKET:EVENT={event_id}",
        f"DATE={date_compact}",
        f"SESSION={session}",
        f"TYPE={tcode}",
    ]
    if pcode:
        parts.append(f"PCODE={pcode}")
    return ":".join(parts)


def build_report_payload(
    job: PaymentJob,
    *,
    pnr_source: str,
    vcc_order_id: str,
    cost_amount: str,
    currency: str,
    paid_at: str | None = None,
    trade_no: str = "",
    sale_price: str = "",
    sale_currency: str = "",
) -> dict[str, Any]:
    if not pnr_source:
        raise ValueError("pnr_source is required (must match server profile)")
    if not vcc_order_id:
        raise ValueError("vcc_order_id is required from create response")

    supplier_order_no = job.custref
    source_ticket_id = f"{supplier_order_no}-01"
    resource_key = build_resource_key(
        event_id=job.event_id or "151991",
        date=job.date,
        time_slot=job.time,
        tcode=job.tcode,
        pcode=job.pcode,
    )

    credential: dict[str, Any] = {
        "custref": job.custref,
        "buyer_email": job.account_email,
        "travel_date": job.date,
        "session_time": job.time,
        "tcode": job.tcode,
        "pcode": job.pcode,
        "event_id": job.event_id or "151991",
        "ticket_count": job.ticket_count,
        "shop": job.shop,
        "credential_issue": "",
        "credential_detail_count": 0,
    }
    # Vivaticket often emails the voucher; keep empty rather than invent.
    if not job.date or not job.tcode:
        credential["credential_issue"] = "incomplete_sale_dims"
    if sale_price:
        credential["sale_price"] = sale_price
        credential["sale_currency"] = sale_currency or currency

    ticket: dict[str, Any] = {
        "source_ticket_id": source_ticket_id,
        "cost_amount": cost_amount,
        "credential": credential,
    }
    if resource_key:
        ticket["resource_key"] = resource_key
    if job.date:
        ticket["attributes"] = {"use_date": job.date}

    payload: dict[str, Any] = {
        "pnr_source": pnr_source,
        "supplier_order_no": supplier_order_no,
        "vcc_order_id": vcc_order_id,
        "currency": currency,
        "paid_at": paid_at or datetime.now(timezone.utc).isoformat(),
        "tickets": [ticket],
    }
    if trade_no:
        payload["trade_no"] = trade_no
    return payload


def submit_payment_report(
    client: VccClient,
    store: VccStore,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    Persist first payload, then POST /api/v2/procurement-payment-events.
    Retries must replay the identical body.
    """
    vcc_order_id = str(payload["vcc_order_id"])
    store.save_report_payload(vcc_order_id, payload)
    body = store.load_report_payload(vcc_order_id) or payload

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp, status = client.report_payment(body)
        except VccError as exc:
            last_exc = exc
            if exc.http_status == 409 or exc.code in {
                "idempotency_conflict",
                "state_conflict",
            }:
                raise VccManualRequired(
                    str(exc),
                    code=exc.code or "idempotency_conflict",
                    http_status=exc.http_status,
                    trace_id=exc.trace_id,
                ) from exc
            if attempt < max_attempts and (
                exc.retryable or (exc.http_status or 0) >= 500
            ):
                time.sleep(1)
                continue
            raise

        if status == 409:
            err = resp.get("error") or {}
            raise VccManualRequired(
                str(err.get("message") or "report conflict"),
                code=str(err.get("code") or "idempotency_conflict"),
                http_status=409,
                trace_id=str(resp.get("trace_id") or ""),
            )
        if status >= 500 and attempt < max_attempts:
            time.sleep(1)
            continue
        if status >= 400:
            err = resp.get("error") or {}
            raise VccError(
                str(err.get("message") or f"report HTTP {status}"),
                code=str(err.get("code") or ""),
                http_status=status,
                trace_id=str(resp.get("trace_id") or ""),
            )

        data = resp.get("data") or {}
        purchase_id = str(data.get("purchase_id") or "")
        if status in {200, 202} and purchase_id:
            logger.info(
                "[VCC][支付上报] 服务端已受理 purchase_id=%s txn=%s",
                purchase_id,
                data.get("payment_transaction_id"),
            )
            return data
        raise VccManualRequired(
            "上报返回 2xx 但缺少 purchase_id，结果不确定，转人工",
            code="report_uncertain",
            http_status=status,
            trace_id=str(resp.get("trace_id") or ""),
        )

    raise VccManualRequired(
        f"payment report retries exhausted: {last_exc}",
        code="report_exhausted",
    )
