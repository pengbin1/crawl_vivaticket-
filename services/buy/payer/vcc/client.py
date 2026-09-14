from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from payer.vcc.errors import VccEnvMismatch, VccError
from shared.log import get_logger

logger = get_logger(__name__)


class VccClient:
    """Thin HTTP client. Never logs response bodies that may contain PAN/CVV/OTP."""

    def __init__(
        self,
        base_url: str,
        *,
        expected_env: str = "online",
        timeout: float = 30.0,
        client_cert: str = "",
        client_key: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.expected_env = (expected_env or "").strip()
        self.timeout = timeout
        self._session = requests.Session()
        if client_cert and client_key:
            self._session.cert = (client_cert, client_key)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", expect_data=False)

    def ensure_env(self) -> str:
        payload = self.health()
        env = str(payload.get("env") or "")
        if self.expected_env and env and env != self.expected_env:
            raise VccEnvMismatch(
                f"VCC env mismatch: got={env} expected={self.expected_env}",
                code="env_mismatch",
            )
        return env

    def create_application(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        return self._request_status("POST", "/api/v1/vcc-card-applications", json_body=body)

    def get_application(self, identifier: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/api/v1/vcc-card-applications/{identifier}"
        )

    def refresh_application(self, identifier: str) -> tuple[dict[str, Any], int]:
        return self._request_status(
            "POST",
            f"/api/v1/vcc-card-applications/{identifier}/refresh",
            json_body={},
        )

    def sensitive_details(self, identifier: str) -> dict[str, Any]:
        # Intentionally no body/response logging.
        return self._request(
            "POST",
            f"/api/v1/vcc-card-applications/{identifier}/sensitive-details",
            json_body={},
            sensitive=True,
        )

    def register_otp(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/otp-payments", json_body=body)

    def wait_otp(
        self, payment_id: str, timeout_seconds: int = 20
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/otp-payments/{payment_id}/wait",
            json_body={"timeout_seconds": timeout_seconds},
            sensitive=True,
            timeout=max(self.timeout, float(timeout_seconds) + 15.0),
        )

    def cancel_otp(self, payment_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/otp-payments/{payment_id}/cancel",
            json_body={},
        )

    def report_payment(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        return self._request_status(
            "POST",
            "/api/v2/procurement-payment-events",
            json_body=body,
        )

    def get_purchase(self, purchase_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/purchases/{purchase_id}")

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expect_data: bool = True,
        sensitive: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        payload, status = self._request_status(
            method,
            path,
            json_body=json_body,
            expect_data=expect_data,
            sensitive=sensitive,
            timeout=timeout,
        )
        if status >= 400:
            err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
            raise VccError(
                str(err.get("message") or f"VCC HTTP {status}"),
                code=str(err.get("code") or ""),
                http_status=status,
                trace_id=str(payload.get("trace_id") or ""),
                retryable=status in {502, 503, 524} or status >= 500,
            )
        return payload

    def _request_status(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expect_data: bool = True,
        sensitive: bool = False,
        timeout: float | None = None,
    ) -> tuple[dict[str, Any], int]:
        url = self._url(path)
        started = time.monotonic()
        headers = {"Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self._session.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            step = _step_name(path)
            logger.warning(
                "[VCC][%s] 网络异常 method=%s path=%s err=%s",
                _step_cn(step),
                method,
                path,
                type(exc).__name__,
            )
            raise VccError(str(exc), code="network_error", retryable=True) from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        trace = resp.headers.get("X-Trace-ID") or ""
        req_id = resp.headers.get("X-Request-ID") or ""
        try:
            payload = resp.json() if resp.content else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        env = str(payload.get("env") or "")
        if (
            self.expected_env
            and env
            and env != self.expected_env
            and resp.status_code < 500
        ):
            raise VccEnvMismatch(
                f"VCC 环境不匹配 path={path} 实际={env} 期望={self.expected_env}",
                code="env_mismatch",
                http_status=resp.status_code,
                trace_id=str(payload.get("trace_id") or trace),
            )

        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(err.get("code") or "")
        step = _step_name(path)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        # 开卡/查卡可落盘的非敏感字段，便于排查；敏感接口不打 data 详情
        extra = ""
        if not sensitive and data:
            extra = (
                f" application_id={data.get('application_id') or '-'} "
                f"order_id={data.get('order_id') or '-'} "
                f"status={data.get('status') or '-'} "
                f"card_id={data.get('card_id') or '-'} "
                f"holder={data.get('cardholder_name') or '-'}"
            )
        elif not sensitive and step == "PAYMENT_REPORT" and data:
            extra = (
                f" purchase_id={data.get('purchase_id') or '-'} "
                f"txn={data.get('payment_transaction_id') or '-'}"
            )
        logger.info(
            "[VCC][%s] http=%s code=%s env=%s trace_id=%s req_id=%s "
            "耗时=%sms%s",
            _step_cn(step),
            resp.status_code,
            code or "-",
            env or "-",
            payload.get("trace_id") or trace or "-",
            req_id or "-",
            elapsed_ms,
            extra,
        )
        if expect_data and "data" not in payload and resp.status_code < 400:
            # health is the documented exception
            if path.rstrip("/").endswith("health"):
                return payload, resp.status_code
        return payload, resp.status_code


def _step_name(path: str) -> str:
    p = path.lower()
    if p.endswith("/health"):
        return "HEALTH"
    if "sensitive-details" in p:
        return "VCC_SENSITIVE_DETAILS"
    if p.endswith("/refresh"):
        return "VCC_REFRESH"
    if "lookup-by-card-number" in p:
        return "VCC_LOOKUP"
    if "/vcc-card-applications" in p:
        if p.rstrip("/").endswith("applications"):
            return "VCC_CREATE"
        return "VCC_GET"
    if "/otp-payments" in p and p.endswith("/wait"):
        return "OTP_WAIT"
    if "/otp-payments" in p and p.endswith("/cancel"):
        return "OTP_CANCEL"
    if "/otp-payments" in p:
        return "OTP_REGISTER"
    if "procurement-payment-events" in p:
        return "PAYMENT_REPORT"
    if "/purchases/" in p:
        return "PURCHASE_GET"
    return "VCC_HTTP"


_STEP_CN = {
    "HEALTH": "健康检查",
    "VCC_CREATE": "开卡申请",
    "VCC_GET": "查卡状态",
    "VCC_REFRESH": "刷新卡状态",
    "VCC_SENSITIVE_DETAILS": "取完整卡信息",
    "VCC_LOOKUP": "卡号反查",
    "OTP_REGISTER": "登记3DS验证码",
    "OTP_WAIT": "等待验证码",
    "OTP_CANCEL": "取消验证码等待",
    "PAYMENT_REPORT": "支付成功上报",
    "PURCHASE_GET": "查询采购记录",
    "VCC_HTTP": "VCC请求",
}


def _step_cn(step: str) -> str:
    return _STEP_CN.get(step, step)


def _redact_path(path: str) -> str:
    # Keep template shape without logging identifiers next to sensitive calls.
    if "sensitive-details" in path:
        return "/api/v1/vcc-card-applications/{id}/sensitive-details"
    if "/wait" in path:
        return "/api/v1/otp-payments/{payment_id}/wait"
    return path


def dump_json_safe(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
