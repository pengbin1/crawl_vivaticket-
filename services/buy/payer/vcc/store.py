from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.log import get_logger

logger = get_logger(__name__)

# Fields that must never be written to disk.
_FORBIDDEN_KEYS = {
    "card_number",
    "number",
    "cvv",
    "cvc",
    "code",
    "otp",
    "pan",
}


class VccStore:
    """Persist client_request_id / create body / report payload (no PAN/CVV/OTP)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def application_path(self, client_request_id: str) -> Path:
        safe = _safe_name(client_request_id)
        return self.root / f"app_{safe}.json"

    def report_path(self, vcc_order_id: str) -> Path:
        safe = _safe_name(vcc_order_id)
        return self.root / f"report_{safe}.json"

    def save_application(self, record: dict[str, Any]) -> Path:
        _assert_safe(record)
        client_request_id = str(record["client_request_id"])
        path = self.application_path(client_request_id)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "[VCC][落盘] 开卡幂等键已保存 client_request_id=%s "
            "application_id=%s order_id=%s status=%s 文件=%s",
            client_request_id,
            record.get("application_id") or "-",
            record.get("order_id") or "-",
            record.get("status") or "-",
            path.name,
        )
        return path

    def load_application(self, client_request_id: str) -> dict[str, Any] | None:
        path = self.application_path(client_request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_application(self, client_request_id: str, **fields: Any) -> dict[str, Any]:
        record = self.load_application(client_request_id) or {
            "client_request_id": client_request_id
        }
        record.update(fields)
        _assert_safe(record)
        self.save_application(record)
        return record

    def save_report_payload(self, vcc_order_id: str, payload: dict[str, Any]) -> Path:
        _assert_safe(payload)
        path = self.report_path(vcc_order_id)
        if path.exists():
            # First snapshot wins for idempotent retries.
            return path
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "[VCC][落盘] 首次支付上报快照已保存 vcc_order_id=%s "
            "supplier_order_no=%s 文件=%s",
            vcc_order_id,
            payload.get("supplier_order_no") or "-",
            path.name,
        )
        return path

    def load_report_payload(self, vcc_order_id: str) -> dict[str, Any] | None:
        path = self.report_path(vcc_order_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def loss_path(self, request_id: str) -> Path:
        return self.root / f"loss_{_safe_name(request_id)}.json"

    def loss_result_path(self, request_id: str) -> Path:
        return self.root / f"loss_result_{_safe_name(request_id)}.json"

    def save_loss_payload(self, request_id: str, payload: dict[str, Any]) -> Path:
        _assert_safe(payload)
        path = self.loss_path(request_id)
        if path.exists():
            return path
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "[VCC][落盘] 首次损耗请求快照已保存 request_id=%s asset_id=%s 文件=%s",
            request_id,
            payload.get("asset_id") or "-",
            path.name,
        )
        return path

    def load_loss_payload(self, request_id: str) -> dict[str, Any] | None:
        path = self.loss_path(request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_loss_result(self, request_id: str, data: dict[str, Any]) -> Path:
        _assert_safe(data)
        path = self.loss_result_path(request_id)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:180]


def _assert_safe(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if key_l in _FORBIDDEN_KEYS:
                raise ValueError(f"refusing to persist sensitive field {path}.{key}")
            _assert_safe(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_safe(item, f"{path}[{i}]")
