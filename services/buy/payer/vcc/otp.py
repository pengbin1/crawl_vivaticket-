from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from payer.vcc.client import VccClient
from payer.vcc.errors import VccError, VccManualRequired
from shared.log import get_logger

logger = get_logger(__name__)


def build_payment_id(custref: str) -> str:
    safe = "".join(c for c in (custref or "") if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("custref required for OTP payment_id")
    return f"PAY-{safe}"


def utc_started_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_otp_context(
    client: VccClient,
    *,
    payment_id: str,
    card_id: str,
    card_last4: str,
    amount: str,
    currency: str,
    started_at: str,
    expected_merchant: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "payment_id": payment_id,
        "card_id": card_id,
        "card_last4": card_last4,
        "amount": amount,
        "currency": currency,
        "started_at": started_at,
    }
    if expected_merchant:
        body["expected_merchant"] = expected_merchant
    # Do not pass mailbox_id / cardholder email — server routes by card_id.
    return client.register_otp(body)


def wait_for_otp_code(
    client: VccClient,
    payment_id: str,
    *,
    overall_deadline: float,
    started_at_epoch: float,
    max_age_seconds: int = 600,
    per_wait_seconds: int = 20,
) -> str:
    """
    Bounded wait using the same payment_id. Returns OTP code in memory only.
    Never logs the code.
    """
    while True:
        now = time.time()
        if now >= overall_deadline:
            raise VccError("OTP wait stopped: payment deadline", code="otp_deadline")
        if now - started_at_epoch >= max_age_seconds:
            raise VccError(
                "OTP wait stopped: started_at + max_age exceeded",
                code="otp_expired",
            )
        try:
            payload = client.wait_otp(payment_id, timeout_seconds=per_wait_seconds)
        except VccError as exc:
            if exc.retryable or (exc.http_status or 0) in {502, 503, 524}:
                logger.warning(
                    "[VCC][等待验证码] 传输异常，继续同 payment_id=%s http=%s",
                    payment_id,
                    exc.http_status,
                )
                continue
            raise

        data = payload.get("data") or {}
        status = str(data.get("status") or "").upper()
        if status == "MATCHED":
            code = str(data.get("code") or "")
            if not code:
                raise VccError("OTP MATCHED but empty code", code="otp_empty")
            logger.info(
                "[VCC][等待验证码] 已匹配 payment_id=%s（验证码不写日志）",
                payment_id,
            )
            return code
        if status == "TIMEOUT":
            logger.info(
                "[VCC][等待验证码] 本轮超时，继续等待 payment_id=%s",
                payment_id,
            )
            continue
        if status in {"AMBIGUOUS", "PARSE_FAILED"}:
            raise VccManualRequired(
                f"OTP {status}；禁止猜码，转人工",
                code=status.lower(),
                trace_id=str(payload.get("trace_id") or ""),
            )
        raise VccManualRequired(
            f"unexpected OTP status={status}",
            code=status.lower() or "otp_unknown",
        )
