from __future__ import annotations

import os
import sys
import time
from typing import Any

import requests
from DrissionPage import ChromiumOptions, ChromiumPage

from shared.config import AppConfig
from shared.log import get_logger

logger = get_logger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def build_chromium_options(headless: bool = True, proxy: str = "") -> ChromiumOptions:
    co = ChromiumOptions()
    if headless:
        co.headless(True)
    if proxy:
        co.set_proxy(proxy)
    chromium_bin = os.getenv("CHROMIUM_BIN")
    if chromium_bin:
        try:
            co.set_browser_path(chromium_bin)
        except Exception:
            pass
    if sys.platform.startswith("linux"):
        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
        ):
            try:
                co.set_argument(arg)
            except Exception:
                pass
    return co


def click_queue_it_rejoin_if_present(page: ChromiumPage) -> bool:
    try:
        if not page.wait.eles_loaded("#MainPart_divWarningBox", timeout=1):
            return False
        box = page.ele("#MainPart_divWarningBox", timeout=3)
        if not box:
            return False
        box.click()
        logger.info("[waf] clicked Queue-it rejoin")
        try:
            page.wait.load_start(timeout=5)
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.debug("[waf] queue rejoin skip: %s", exc)
        return False


def drission_cookies_to_session(page: ChromiumPage) -> requests.Session:
    session = requests.Session()
    try:
        ua = page.run_js("return navigator.userAgent")
        if ua:
            session.headers["User-Agent"] = str(ua)
    except Exception:
        session.headers["User-Agent"] = DEFAULT_UA

    for c in page.cookies(all_domains=True, all_info=True):
        name = c.get("name")
        value = c.get("value")
        if not name:
            continue
        domain = c.get("domain") or None
        path = c.get("path") or "/"
        try:
            session.cookies.set(name, value, domain=domain, path=path)
        except Exception:
            session.cookies.set(name, value)
    return session


def session_cookie_dict(session: requests.Session) -> dict[str, str]:
    return {c.name: c.value for c in session.cookies}


def bootstrap_waf(cfg: AppConfig, url: str | None = None) -> tuple[requests.Session, ChromiumPage]:
    """Open event page with Drission, pass WAF, return HTTP session + live page."""
    target = url or cfg.event_url
    page = ChromiumPage(build_chromium_options(cfg.headless, cfg.proxy))
    try:
        page.set.load_mode.eager()
    except Exception:
        pass

    last_html = ""
    for attempt in range(1, 4):
        t0 = time.perf_counter()
        page.get(target, timeout=cfg.browser_get_timeout)
        click_queue_it_rejoin_if_present(page)
        # Give Safetynet/queue a moment to settle into qubs* URL
        time.sleep(1.5)
        last_html = page.html or ""
        logger.info(
            "[waf] attempt=%s loaded in %.2fs url=%s html_len=%s",
            attempt,
            time.perf_counter() - t0,
            page.url,
            len(last_html),
        )
        if "Incapsula" in last_html and len(last_html) < 1500:
            logger.warning("[waf] still Incapsula, retry")
            continue
        if "eventi[" not in last_html:
            logger.warning("[waf] no eventi calendar yet, retry")
            continue
        # Prefer landing that includes Safetynet query (usually fuller inventory)
        if "qubsq=" in (page.url or "") or len(last_html) > 45000:
            break
        logger.info("[waf] no qubs yet / short html, retry once more")
    else:
        if "Incapsula" in last_html and len(last_html) < 1500:
            raise RuntimeError("仍停留在 Incapsula 挑战页，WAF 未过")
        if "eventi[" not in last_html:
            raise RuntimeError("活动页未出现日历 eventi 数据")

    session = drission_cookies_to_session(page)
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    if cfg.proxy:
        session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})
    return session, page
