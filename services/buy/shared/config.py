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
class VccConfig:
    """Dings VCC ticket API — see crawler-vcc-payment-api-docs."""

    enabled: bool = False
    base_url: str = ""
    expected_env: str = "online"
    # Must match deploy VCC_CARD_AMOUNT / VCC_CARD_CURRENCY (not the ticket price alone).
    card_amount: str = ""
    card_currency: str = "EUR"
    card_bin: str = "NORMAL"
    # Confirm MCC with payment team before setting; empty = omit (upstream default).
    allowed_merchant_categories: str = ""
    # Must match a server-side profile; do not copy small_train_official blindly.
    pnr_source: str = ""
    pay_currency: str = "EUR"
    fallback_cost_amount: str = ""
    expected_merchant: str = ""
    persist_dir: str = ""
    timeout_seconds: float = 30.0
    client_cert: str = ""
    client_key: str = ""
    otp_max_age_seconds: int = 600
    # Debug / unsellable tickets: after procurement, confirm LOSS (cannot sell, no refund).
    auto_confirm_loss: bool = False
    loss_reason: str = "debug_unsellable_no_refund"


@dataclass
class PayQueueConfig:
    """Local file queue + optional HTTP wake for pay worker."""

    enabled: bool = True
    queue_dir: str = ""  # default: $CENACOLO_HOME/var/pay_queue
    # Lock worker POSTs here after enqueue, e.g. http://127.0.0.1:18765/wake
    wake_url: str = "http://127.0.0.1:18765/wake"
    wake_listen_host: str = "127.0.0.1"
    wake_listen_port: int = 18765
    # Even with queue, periodically scan Mongo locked (dual insurance).
    mongo_fallback_seconds: float = 15.0


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
    vcc: VccConfig
    pay_queue: PayQueueConfig
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

    vcc_raw = _deep_get(data, "payment", "vcc", default={}) or {}
    vcc = VccConfig(
        enabled=bool(
            os.getenv("CENACOLO_VCC_ENABLED", str(vcc_raw.get("enabled") or "false")).lower()
            in {"1", "true", "yes", "on"}
        ),
        base_url=str(
            os.getenv("CENACOLO_VCC_BASE_URL")
            or vcc_raw.get("base_url")
            or "https://dings.133.cn/online/dings_vcc_ticket"
        ).rstrip("/"),
        expected_env=str(
            os.getenv("CENACOLO_VCC_ENV") or vcc_raw.get("expected_env") or "online"
        ),
        card_amount=str(
            os.getenv("CENACOLO_VCC_CARD_AMOUNT") or vcc_raw.get("card_amount") or ""
        ),
        card_currency=str(
            os.getenv("CENACOLO_VCC_CARD_CURRENCY")
            or vcc_raw.get("card_currency")
            or "EUR"
        ).upper(),
        card_bin=str(vcc_raw.get("card_bin") or "NORMAL"),
        allowed_merchant_categories=str(
            vcc_raw.get("allowed_merchant_categories") or ""
        ),
        pnr_source=str(
            os.getenv("CENACOLO_VCC_PNR_SOURCE") or vcc_raw.get("pnr_source") or ""
        ),
        pay_currency=str(vcc_raw.get("pay_currency") or "EUR").upper(),
        fallback_cost_amount=str(vcc_raw.get("fallback_cost_amount") or ""),
        expected_merchant=str(vcc_raw.get("expected_merchant") or ""),
        persist_dir=str(
            os.getenv("CENACOLO_VCC_PERSIST_DIR") or vcc_raw.get("persist_dir") or ""
        ),
        timeout_seconds=float(vcc_raw.get("timeout_seconds") or 30),
        client_cert=str(
            os.getenv("CENACOLO_VCC_CLIENT_CERT") or vcc_raw.get("client_cert") or ""
        ),
        client_key=str(
            os.getenv("CENACOLO_VCC_CLIENT_KEY") or vcc_raw.get("client_key") or ""
        ),
        otp_max_age_seconds=int(vcc_raw.get("otp_max_age_seconds") or 600),
        auto_confirm_loss=bool(
            os.getenv(
                "CENACOLO_VCC_AUTO_LOSS",
                str(vcc_raw.get("auto_confirm_loss") or "false"),
            ).lower()
            in {"1", "true", "yes", "on"}
        ),
        loss_reason=str(
            vcc_raw.get("loss_reason") or "debug_unsellable_no_refund"
        ),
    )

    pq_raw = _deep_get(data, "payment", "pay_queue", default={}) or {}
    pay_queue = PayQueueConfig(
        enabled=bool(
            str(pq_raw.get("enabled", True)).lower() not in {"0", "false", "no", "off"}
        ),
        queue_dir=str(
            os.getenv("CENACOLO_PAY_QUEUE_DIR") or pq_raw.get("queue_dir") or ""
        ),
        wake_url=str(
            os.getenv("CENACOLO_PAY_WAKE_URL")
            or pq_raw.get("wake_url")
            or "http://127.0.0.1:18765/wake"
        ),
        wake_listen_host=str(pq_raw.get("wake_listen_host") or "127.0.0.1"),
        wake_listen_port=int(pq_raw.get("wake_listen_port") or 18765),
        mongo_fallback_seconds=float(pq_raw.get("mongo_fallback_seconds") or 15),
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
        vcc=vcc,
        pay_queue=pay_queue,
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
