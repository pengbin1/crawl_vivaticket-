"""Bootstrap Incapsula cookies for secure.vivaticket.com via Drission."""

from __future__ import annotations

import time

import requests
from DrissionPage import ChromiumPage

from locker.waf import build_chromium_options, drission_cookies_to_session
from shared.log import get_logger

logger = get_logger(__name__)

SECURE_HOST = "secure.vivaticket.com"


def bootstrap_secure_session(
    payment_url: str,
    *,
    headless: bool = True,
    proxy: str = "",
    timeout: float = 45.0,
) -> tuple[requests.Session, ChromiumPage]:
    """
    Open formtr with Drission, pass Incapsula, return HTTP session + page.

    Bare requests to secure.vivaticket.com get a ~212B Incapsula shell;
    exported cookies are required before http_pay can run formtr→doauth.
    """
    page = ChromiumPage(build_chromium_options(headless, proxy))
    try:
        page.set.load_mode.eager()
    except Exception:
        pass

    page.get(payment_url, timeout=timeout)
    time.sleep(2.0)
    html = page.html or ""
    url = page.url or ""

    logger.info("[secure_waf] url=%s html_len=%s", url, len(html))

    if SECURE_HOST not in url:
        raise RuntimeError(
            f"支付链接已失效或已跳转（当前 {url}）。"
            "custref 20 分钟窗口过期后会回到活动页。"
        )
    if "Incapsula" in html and len(html) < 1500:
        raise RuntimeError("secure.vivaticket.com 仍停留在 Incapsula 挑战页")

    session = drission_cookies_to_session(page)
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session, page
