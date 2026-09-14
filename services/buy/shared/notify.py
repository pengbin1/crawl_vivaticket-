from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import requests

from shared.log import get_logger

logger = get_logger(__name__)


def notify(text: str, webhook: str = "", secret: str = "") -> None:
    if not webhook:
        logger.info("[notify] %s", text)
        return
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    try:
        resp = requests.post(webhook, json=payload, timeout=15)
        logger.info("[notify] feishu status=%s", resp.status_code)
    except Exception as exc:
        logger.warning("[notify] feishu failed: %s", exc)


def dump_json(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
