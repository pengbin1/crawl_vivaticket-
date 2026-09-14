from __future__ import annotations

import random
import time
from typing import Any

from payer.vcc.client import VccClient
from payer.vcc.errors import VccError, VccManualRequired
from payer.vcc.models import VccApplication, VccSensitiveCard
from payer.vcc.store import VccStore
from shared.log import get_logger

logger = get_logger(__name__)

_OK_PAY_STATUSES = {"CREATED", "ACTIVE"}
_REFRESH_STATUSES = {"UNKNOWN", "PENDING"}
_DEAD_STATUSES = {
    "FAILED",
    "INACTIVE",
    "BLOCKED",
    "SUSPENDED",
    "CLOSED",
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "TERMINATED",
}
_REFRESH_BACKOFF = (3, 5, 10, 20)


def build_client_request_id(custref: str, attempt: int = 1) -> str:
    """Stable per lock/payment attempt. Do not change amount/currency/MCC under same key."""
    safe = "".join(c for c in (custref or "UNKNOWN") if c.isalnum() or c in "-_")
    return f"CENACOLO-CARD-{safe}-{int(attempt)}"


def open_vcc_for_payment(
    client: VccClient,
    store: VccStore,
    *,
    client_request_id: str,
    amount: str,
    currency: str,
    card_bin: str = "NORMAL",
    allowed_merchant_categories: str = "",
) -> VccApplication:
    """
    Persist key → create (idempotent replay) → recover UNKNOWN/PENDING via refresh only.
    """
    body: dict[str, Any] = {
        "client_request_id": client_request_id,
        "amount": amount,
        "currency": currency,
        "card_bin": card_bin or "NORMAL",
    }
    if allowed_merchant_categories:
        body["allowed_merchant_categories"] = allowed_merchant_categories

    existing = store.load_application(client_request_id)
    if existing and existing.get("create_body"):
        # Never change facts under the same key.
        body = dict(existing["create_body"])
        if body.get("client_request_id") != client_request_id:
            raise VccManualRequired(
                "stored create_body client_request_id mismatch",
                code="idempotency_conflict",
            )
    else:
        store.save_application(
            {
                "client_request_id": client_request_id,
                "create_body": body,
                "status": "INIT",
            }
        )

    logger.info(
        "[VCC][开卡申请] 即将请求开卡 client_request_id=%s amount=%s %s BIN=%s",
        client_request_id,
        amount,
        currency,
        body.get("card_bin"),
    )
    client.ensure_env()
    app = _create_with_retries(client, store, body)
    logger.info(
        "[VCC][开卡申请] 返回 status=%s application_id=%s order_id=%s card_id=%s",
        app.status,
        app.application_id or "-",
        app.order_id or "-",
        app.card_id or "-",
    )
    status = (app.status or "").upper()

    if status == "CREATING":
        app = _poll_creating(client, store, app)
        status = (app.status or "").upper()

    if status in _REFRESH_STATUSES:
        app = recover_application(client, store, app)
        status = (app.status or "").upper()

    if status in _OK_PAY_STATUSES:
        return app
    if status in _DEAD_STATUSES or status == "FAILED":
        raise VccManualRequired(
            f"VCC application not payable status={status}",
            code=status.lower() or "failed",
            trace_id=app.trace_id,
        )
    raise VccManualRequired(
        f"VCC application unresolved status={status}",
        code=status.lower() or "unresolved",
        trace_id=app.trace_id,
    )


def recover_application(
    client: VccClient,
    store: VccStore,
    app: VccApplication,
) -> VccApplication:
    """Bounded refresh on original application_id/order_id. Never re-create."""
    identifier = app.application_id or app.order_id
    if not identifier:
        raise VccManualRequired(
            "cannot refresh without application_id/order_id",
            code="not_found",
        )

    for attempt, delay in enumerate(_REFRESH_BACKOFF, start=1):
        try:
            payload, status = client.refresh_application(identifier)
        except VccError as exc:
            if exc.code == "vcc_identity_conflict" or exc.http_status == 409:
                raise VccManualRequired(
                    str(exc),
                    code=exc.code or "vcc_identity_conflict",
                    http_status=exc.http_status,
                    trace_id=exc.trace_id,
                ) from exc
            if not exc.retryable and exc.code not in {
                "vcc_card_not_ready",
                "vcc_upstream_unavailable",
                "vcc_upstream_invalid_response",
                "storage_busy",
            }:
                raise
            logger.warning(
                "[VCC][刷新卡状态] 第%s次重试 code=%s，保留原申请不重开",
                attempt,
                exc.code or exc.http_status,
            )
            _sleep_backoff(delay)
            continue

        data = payload.get("data") or {}
        app = _app_from_data(
            app.client_request_id,
            data,
            create_body=app.create_body,
            trace_id=str(payload.get("trace_id") or ""),
            env=str(payload.get("env") or ""),
        )
        _persist_app(store, app)
        st = (app.status or "").upper()
        if st in _OK_PAY_STATUSES:
            return app
        if st in _DEAD_STATUSES:
            raise VccManualRequired(
                f"VCC refresh converged to {st}",
                code=st.lower(),
                trace_id=app.trace_id,
            )
        if status == 200 and st not in _REFRESH_STATUSES and st != "CREATING":
            return app
        _sleep_backoff(delay)

    raise VccManualRequired(
        "VCC refresh exhausted; keep original application for ops",
        code="refresh_exhausted",
        trace_id=app.trace_id,
    )


def fetch_sensitive_card(
    client: VccClient,
    app: VccApplication,
) -> VccSensitiveCard:
    identifier = app.application_id or app.order_id
    if not identifier:
        raise VccError("missing application identifier for sensitive-details")
    if not app.card_id:
        # Docs: if no trusted card_id, refresh first or sensitive returns 409.
        raise VccError(
            "application has no card_id; refresh before sensitive-details",
            code="state_conflict",
            retryable=True,
        )

    payload = client.sensitive_details(identifier)
    data = payload.get("data") or {}
    card_status = str(data.get("card_status") or "").upper()
    if card_status != "ACTIVE":
        raise VccManualRequired(
            f"card_status={card_status} not ACTIVE",
            code="card_not_active",
            trace_id=str(payload.get("trace_id") or ""),
        )
    number = str(data.get("card_number") or "")
    cvv = str(data.get("cvv") or "")
    if not number or not cvv:
        raise VccError("sensitive-details missing card_number/cvv", code="invalid_response")
    return VccSensitiveCard(
        card_id=str(data.get("card_id") or app.card_id or ""),
        number=number,
        cvv=cvv,
        expiry_month=str(data.get("expiry_month") or ""),
        expiry_year=str(data.get("expiry_year") or ""),
        card_status=card_status,
        card_bin=str(data.get("card_bin") or ""),
    )


def _create_with_retries(
    client: VccClient,
    store: VccStore,
    body: dict[str, Any],
) -> VccApplication:
    client_request_id = str(body["client_request_id"])
    last_exc: Exception | None = None
    for attempt in range(1, 6):
        try:
            payload, status = client.create_application(body)
        except VccError as exc:
            last_exc = exc
            if exc.code == "vcc_create_busy" or (
                exc.http_status == 503 and exc.code in {"vcc_create_busy", "storage_busy"}
            ):
                logger.warning(
                    "[VCC][开卡申请] 并发忙，第%s次退避重试 code=%s（不换幂等键）",
                    attempt,
                    exc.code,
                )
                _sleep_backoff(3)
                continue
            if exc.retryable or exc.code == "network_error":
                logger.warning(
                    "[VCC][开卡申请] 第%s次重试 code=%s（原样重放，不换键）",
                    attempt,
                    exc.code,
                )
                _sleep_backoff(min(20, 2 * attempt))
                continue
            if exc.code == "idempotency_conflict" or exc.http_status == 409:
                raise VccManualRequired(
                    str(exc),
                    code=exc.code or "idempotency_conflict",
                    http_status=exc.http_status,
                    trace_id=exc.trace_id,
                ) from exc
            raise

        if status == 503:
            err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
            code = str(err.get("code") or "")
            if code in {"vcc_create_busy", "storage_busy"}:
                _sleep_backoff(3)
                continue
        if status >= 400:
            err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
            raise VccError(
                str(err.get("message") or f"create HTTP {status}"),
                code=str(err.get("code") or ""),
                http_status=status,
                trace_id=str(payload.get("trace_id") or ""),
                retryable=status in {502, 503},
            )

        data = payload.get("data") or {}
        app = _app_from_data(
            client_request_id,
            data,
            create_body=body,
            trace_id=str(payload.get("trace_id") or ""),
            env=str(payload.get("env") or ""),
        )
        _persist_app(store, app)
        return app

    raise VccManualRequired(
        f"VCC create retries exhausted: {last_exc}",
        code="create_exhausted",
    )


def _poll_creating(
    client: VccClient,
    store: VccStore,
    app: VccApplication,
) -> VccApplication:
    identifier = app.application_id or app.order_id
    for attempt in range(1, 8):
        _sleep_backoff(min(10, attempt * 2))
        payload = client.get_application(identifier)
        data = payload.get("data") or {}
        app = _app_from_data(
            app.client_request_id,
            data,
            create_body=app.create_body,
            trace_id=str(payload.get("trace_id") or ""),
            env=str(payload.get("env") or ""),
        )
        _persist_app(store, app)
        st = (app.status or "").upper()
        if st != "CREATING":
            return app
        logger.info(
            "[VCC][查卡状态] 仍在 CREATING，第%s次轮询 application_id=%s",
            attempt,
            identifier,
        )
    return app


def _app_from_data(
    client_request_id: str,
    data: dict[str, Any],
    *,
    create_body: dict[str, Any],
    trace_id: str = "",
    env: str = "",
) -> VccApplication:
    return VccApplication(
        client_request_id=client_request_id,
        application_id=str(data.get("application_id") or ""),
        order_id=str(data.get("order_id") or ""),
        provider_request_id=str(data.get("provider_request_id") or ""),
        status=str(data.get("status") or ""),
        card_id=(str(data["card_id"]) if data.get("card_id") is not None else None),
        cardholder_name=str(data.get("cardholder_name") or ""),
        cardholder_email=str(data.get("cardholder_email") or ""),
        create_body=dict(create_body),
        trace_id=trace_id,
        env=env,
    )


def _persist_app(store: VccStore, app: VccApplication) -> None:
    store.update_application(
        app.client_request_id,
        create_body=app.create_body,
        application_id=app.application_id,
        order_id=app.order_id,
        provider_request_id=app.provider_request_id,
        status=app.status,
        card_id=app.card_id,
        cardholder_name=app.cardholder_name,
        cardholder_email=app.cardholder_email,
        trace_id=app.trace_id,
        env=app.env,
    )
    logger.info(
        "[VCC][开卡信息] client_request_id=%s application_id=%s order_id=%s "
        "status=%s card_id=%s holder=%s email=%s provider_request_id=%s trace_id=%s",
        app.client_request_id,
        app.application_id or "-",
        app.order_id or "-",
        app.status or "-",
        app.card_id or "-",
        app.cardholder_name or "-",
        app.cardholder_email or "-",
        app.provider_request_id or "-",
        app.trace_id or "-",
    )


def _sleep_backoff(base_seconds: float) -> None:
    jitter = random.uniform(0, min(1.0, base_seconds * 0.2))
    time.sleep(base_seconds + jitter)
