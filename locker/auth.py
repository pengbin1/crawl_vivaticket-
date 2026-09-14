from __future__ import annotations

import re

import requests
from lxml import html as lxml_html

from shared.config import AppConfig
from shared.log import get_logger

logger = get_logger(__name__)


def _extract_keylogin(html_text: str) -> str | None:
    m = re.search(
        r'name=["\']keylogin["\'][^>]*value=["\']([^"\']+)["\']',
        html_text,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.search(
        r'value=["\']([^"\']+)["\'][^>]*name=["\']keylogin["\']',
        html_text,
        re.I,
    )
    return m.group(1) if m else None


def is_logged_in(html_text: str) -> bool:
    return (
        "/en/logout" in html_text
        or "Log out" in html_text
        or 'id="accountInformation"' in html_text
    )


def login(session: requests.Session, cfg: AppConfig) -> None:
    if not cfg.email or not cfg.password:
        raise ValueError("account.email / password 未配置")

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"{cfg.base}/en/member",
        "User-Agent": session.headers.get("User-Agent", ""),
    }
    resp = session.get(f"{cfg.base}/en/", headers=headers, timeout=60)
    text = resp.text
    if "Incapsula" in text and len(text) < 1500:
        raise RuntimeError("登录页仍是 Incapsula，cookie 无效")

    if is_logged_in(text):
        logger.info("[auth] already logged in")
        return

    keylogin = _extract_keylogin(text)
    if not keylogin:
        tree = lxml_html.fromstring(text)
        values = tree.xpath('//input[@name="keylogin"]/@value')
        keylogin = values[0] if values else None
    if not keylogin:
        raise RuntimeError("未找到 keylogin，可能仍在队列/风控页")

    data = {
        "sorgente": "/en",
        "page": "login",
        "keylogin": keylogin,
        "unam": cfg.email,
        "upwd": cfg.password,
    }
    post = session.post(f"{cfg.base}/index.php", data=data, headers=headers, timeout=30)
    logger.info("[auth] login POST status=%s", post.status_code)

    check = session.get(f"{cfg.base}/en/", headers=headers, timeout=30)
    if not is_logged_in(check.text):
        raise RuntimeError("登录后未检测到 Log out，账号或风控失败")
    logger.info("[auth] login ok: %s", cfg.email)


def clear_cart(session: requests.Session, cfg: AppConfig) -> None:
    resp = session.get(
        f"{cfg.base}/index.php",
        params={"nvpg[sell]": "", "cmd": "cancellaCarrello", "empty": "1"},
        timeout=30,
    )
    logger.info("[auth] clear cart status=%s len=%s", resp.status_code, len(resp.text))
