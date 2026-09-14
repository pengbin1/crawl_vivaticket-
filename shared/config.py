from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shared.models import Passenger

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CardConfig:
    number: str = ""
    expiry: str = ""
    cvv: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""

    def last4(self) -> str:
        digits = "".join(c for c in self.number if c.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""

    def ready(self) -> bool:
        return bool(self.number and self.expiry and self.cvv)


@dataclass
class AppConfig:
    email: str
    password: str
    base: str
    event_path: str
    idt: str
    target_dates: list[str]
    use_any_available: bool
    ticket_count: int
    poll_when_empty: bool
    poll_interval_seconds: int
    passengers: list[Passenger]
    captcha_key: str
    captcha_china: bool
    captcha_timeout: int
    lock_ttl_seconds: int
    http_attempt_timeout_seconds: int
    urgent_remaining_seconds: int
    prefer_browser: bool
    card: CardConfig
    headless: bool
    proxy: str
    browser_get_timeout: int
    feishu_webhook: str
    feishu_secret: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def event_url(self) -> str:
        return f"{self.base.rstrip('/')}{self.event_path}?idt={self.idt}"


def _deep_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path) if path else ROOT / "config.local.yaml"
    if not cfg_path.exists():
        example = ROOT / "config.example.yaml"
        raise FileNotFoundError(
            f"缺少配置 {cfg_path}，请先: cp {example.name} {cfg_path.name}"
        )
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    passengers_raw = data.get("passengers") or []
    passengers = [
        Passenger(
            first_name=str(p.get("first_name", "")).strip(),
            last_name=str(p.get("last_name", "")).strip(),
        )
        for p in passengers_raw
    ]
    if not passengers:
        raise ValueError("config.passengers 不能为空")

    card_raw = _deep_get(data, "payment", "card", default={}) or {}
    card = CardConfig(
        number=os.getenv("CENACOLO_CARD_NUMBER", str(card_raw.get("number") or "")),
        expiry=os.getenv("CENACOLO_CARD_EXPIRY", str(card_raw.get("expiry") or "")),
        cvv=os.getenv("CENACOLO_CARD_CVV", str(card_raw.get("cvv") or "")),
        first_name=str(card_raw.get("first_name") or passengers[0].first_name),
        last_name=str(card_raw.get("last_name") or passengers[0].last_name),
        email=str(
            card_raw.get("email")
            or _deep_get(data, "account", "email", default="")
            or ""
        ),
    )

    email = os.getenv(
        "CENACOLO_EMAIL", str(_deep_get(data, "account", "email", default="") or "")
    )
    password = os.getenv(
        "CENACOLO_PASSWORD",
        str(_deep_get(data, "account", "password", default="") or ""),
    )
    captcha_key = os.getenv(
        "CENACOLO_CAPTCHA_KEY",
        str(_deep_get(data, "captcha", "client_key", default="") or ""),
    )

    return AppConfig(
        email=email,
        password=password,
        base=str(_deep_get(data, "event", "base", default="")).rstrip("/"),
        event_path=str(
            _deep_get(
                data, "event", "path", default="/en/event/cenacolo-vinciano/151991"
            )
        ),
        idt=str(_deep_get(data, "event", "idt", default="2547")),
        target_dates=list(_deep_get(data, "event", "target_dates", default=[]) or []),
        use_any_available=bool(
            _deep_get(data, "event", "use_any_available", default=True)
        ),
        ticket_count=int(_deep_get(data, "event", "ticket_count", default=1) or 1),
        poll_when_empty=bool(
            _deep_get(data, "event", "poll_when_empty", default=False)
        ),
        poll_interval_seconds=int(
            _deep_get(data, "event", "poll_interval_seconds", default=30) or 30
        ),
        passengers=passengers,
        captcha_key=captcha_key,
        captcha_china=bool(
            _deep_get(data, "captcha", "use_china_endpoint", default=False)
        ),
        captcha_timeout=int(
            _deep_get(data, "captcha", "timeout_seconds", default=90) or 90
        ),
        lock_ttl_seconds=int(
            _deep_get(data, "payment", "lock_ttl_seconds", default=1200) or 1200
        ),
        http_attempt_timeout_seconds=int(
            _deep_get(data, "payment", "http_attempt_timeout_seconds", default=45) or 45
        ),
        urgent_remaining_seconds=int(
            _deep_get(data, "payment", "urgent_remaining_seconds", default=120) or 120
        ),
        prefer_browser=bool(
            _deep_get(data, "payment", "prefer_browser", default=True)
        ),
        card=card,
        headless=bool(_deep_get(data, "browser", "headless", default=True)),
        proxy=str(
            os.getenv("VIVATICKET_PROXY")
            or _deep_get(data, "browser", "proxy", default="")
            or ""
        ),
        browser_get_timeout=int(
            _deep_get(data, "browser", "get_timeout", default=25) or 25
        ),
        feishu_webhook=str(
            _deep_get(data, "notify", "feishu_webhook", default="") or ""
        ),
        feishu_secret=str(
            _deep_get(data, "notify", "feishu_secret", default="") or ""
        ),
        raw=data,
    )
