"""Wake notify: lock worker POSTs after enqueue; pay worker listens and wakes immediately."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error, request

from shared.log import get_logger

logger = get_logger(__name__)


def notify_pay_wake(
    wake_url: str,
    *,
    order_id: str,
    order_no: str = "",
    custref: str = "",
    timeout: float = 2.0,
) -> bool:
    """Best-effort HTTP notify. Failure is OK — Mongo/queue fallback still works."""
    url = (wake_url or "").strip()
    if not url:
        return False
    body = json.dumps(
        {"order_id": order_id, "order_no": order_no, "custref": custref},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            ok = 200 <= int(getattr(resp, "status", 200)) < 300
            logger.info(
                "[支付通知] wake 已发送 order_id=%s url=%s ok=%s",
                order_id,
                url,
                ok,
            )
            return ok
    except error.HTTPError as exc:
        logger.warning("[支付通知] wake HTTP %s order_id=%s", exc.code, order_id)
        return False
    except Exception as exc:
        logger.warning(
            "[支付通知] wake 失败（将依赖队列/扫库兜底）order_id=%s err=%s",
            order_id,
            type(exc).__name__,
        )
        return False


class WakeServer:
    """Tiny /wake endpoint that sets an Event for the pay worker loop."""

    def __init__(self, host: str, port: int, wake_event: threading.Event) -> None:
        self.host = host
        self.port = int(port)
        self.wake_event = wake_event
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        wake_event = self.wake_event
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/") in {"/health", "/wake"}:
                    self._ok({"status": "ok"})
                else:
                    self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.rstrip("/") != "/wake":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                wake_event.set()
                logger.info(
                    "[支付通知] 收到 wake order_id=%s custref=%s",
                    payload.get("order_id"),
                    payload.get("custref"),
                )
                self._ok({"accepted": True})

            def _ok(self, data: dict[str, Any]) -> None:
                body = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="pay-wake", daemon=True
        )
        self._thread.start()
        logger.info(
            "[支付通知] 监听 wake 接口 http://%s:%s/wake", self.host, self.port
        )

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
