from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from shared.config import AppConfig
from shared.deadline import assert_time_left, seconds_remaining
from shared.log import get_logger
from shared.models import PayResult, PaymentJob

logger = get_logger(__name__)

SECURE_BASE = "https://secure.vivaticket.com"


@dataclass
class AxerveSession:
    flow_id: str
    auth_token: str
    axerve_url: str
    session: requests.Session


def _resolve_url(base_url: str, location: str | None) -> str | None:
    if not location:
        return None
    return urljoin(base_url, location)


def _extract_hidden_inputs(page_text: str) -> dict[str, str]:
    hidden_fields: dict[str, str] = {}
    pattern = re.compile(
        r'<input[^>]+type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        re.IGNORECASE,
    )
    for name, value in pattern.findall(page_text):
        hidden_fields[name] = html_lib.unescape(value)
    # alternate attribute order
    pattern2 = re.compile(
        r'<input[^>]+name=["\']([^"\']+)["\'][^>]*type=["\']hidden["\'][^>]*value=["\']([^"\']*)["\']',
        re.IGNORECASE,
    )
    for name, value in pattern2.findall(page_text):
        hidden_fields.setdefault(name, html_lib.unescape(value))
    return hidden_fields


def _extract_redirect_from_html(page_text: str, current_url: str) -> str | None:
    patterns = [
        r"""document\.location\s*=\s*['"]([^'"]+)['"]""",
        r"""window\.location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""",
        r"""location\.href\s*=\s*['"]([^'"]+)['"]""",
        r"""<meta[^>]+http-equiv=["']refresh["'][^>]+content=["'][^"']*url=([^"'>]+)""",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            return _resolve_url(current_url, html_lib.unescape(match.group(1)))
    return None


def _follow_to_url(
    session: requests.Session,
    url: str,
    timeout: float,
    referer: str = "",
) -> tuple[requests.Response, str]:
    headers = {"Referer": referer} if referer else None
    resp = session.get(url, allow_redirects=False, timeout=timeout, headers=headers)
    if resp.status_code in (301, 302, 303, 307, 308):
        nxt = _resolve_url(resp.url, resp.headers.get("Location"))
        if not nxt:
            raise RuntimeError(f"redirect without Location: {resp.url}")
        return resp, nxt
    if resp.status_code == 200:
        nxt = _extract_redirect_from_html(resp.text, resp.url)
        if nxt:
            return resp, nxt
        return resp, resp.url
    raise RuntimeError(f"unexpected status {resp.status_code} for {url}")


def open_axerve_from_payment_url(
    payment_url: str,
    custref: str,
    shop: str = "CV0",
    domain: str = "prod-pay-it",
    user_agent: str = "",
    timeout: float = 45.0,
    session: requests.Session | None = None,
) -> AxerveSession:
    """
    Real captured chain (from crawl_vivaticket/pay_.py):
      GET formtr.php → POST doauth.php (payment_mode=300)
      → Sella pagam → Ax_Acceptance_Create → Axerve checkout URL
    Does NOT submit card (final fulfill API not fully captured).
    """
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": user_agent
                or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
            }
        )
    elif user_agent:
        session.headers.setdefault("User-Agent", user_agent)

    logger.info("[http_pay] GET formtr")
    r1 = session.get(payment_url, timeout=timeout, allow_redirects=True)
    if r1.status_code != 200:
        raise RuntimeError(f"formtr status={r1.status_code}")
    fields = _extract_hidden_inputs(r1.text)
    fields.setdefault("custref", custref)
    fields.setdefault("shop", shop)
    if domain:
        fields.setdefault("domain", domain)

    payload = dict(fields)
    payload.update(
        {
            "payment_mode": "300",
            "shop": fields.get("shop", shop),
            "custref": fields.get("custref", custref),
            "paymentMethod": "on",
        }
    )
    if domain:
        payload["domain"] = fields.get("domain", domain)

    logger.info("[http_pay] POST doauth payment_mode=300")
    r2 = session.post(
        f"{SECURE_BASE}/paycmd/doauth.php",
        data=payload,
        allow_redirects=False,
        timeout=timeout,
        headers={"Referer": r1.url, "Content-Type": "application/x-www-form-urlencoded"},
    )
    if r2.status_code in (301, 302, 303, 307, 308):
        sella_url = _resolve_url(r2.url, r2.headers.get("Location"))
    elif r2.status_code == 200:
        sella_url = _extract_redirect_from_html(r2.text, r2.url)
    else:
        raise RuntimeError(f"doauth status={r2.status_code}")
    if not sella_url:
        raise RuntimeError("doauth 未给出下一跳")
    logger.info("[http_pay] sella/next=%s", sella_url)

    # Follow redirects until Axerve checkout URL appears (bounded hops)
    current = sella_url
    axerve_url = ""
    for hop in range(8):
        if "web.axerve.com/orchestra/checkout" in current:
            axerve_url = current
            break
        resp, nxt = _follow_to_url(session, current, timeout)
        logger.info("[http_pay] hop%d %s → %s", hop, resp.status_code, nxt)
        if nxt == current and "web.axerve.com" not in nxt:
            # landed on non-redirect page
            maybe = re.search(
                r"https://web\.axerve\.com/orchestra/checkout/a/[^/\"'\s]+/b/[^/\"'\s]+",
                resp.text or "",
            )
            if maybe:
                axerve_url = maybe.group(0)
                break
            raise RuntimeError(f"卡在中间页: {current}")
        current = nxt
    else:
        if "web.axerve.com/orchestra/checkout" in current:
            axerve_url = current

    if not axerve_url:
        raise RuntimeError(f"未能到达 Axerve URL，最后: {current}")

    m = re.search(r"/a/([^/]+)/b/([^/?#]+)", axerve_url)
    if not m:
        raise RuntimeError(f"Axerve URL 无法解析 flow/token: {axerve_url}")
    flow_id, auth_token = m.group(1), m.group(2)
    logger.info("[http_pay] axerve flowId=%s", flow_id)
    return AxerveSession(
        flow_id=flow_id,
        auth_token=auth_token,
        axerve_url=axerve_url,
        session=session,
    )


def try_http_pay(
    job: PaymentJob,
    cfg: AppConfig,
    http_session: requests.Session | None = None,
) -> PayResult:
    """
    Advance payment as far as captured HTTP allows.
    Card fulfill is NOT implemented as pure HTTP yet → returns needs_browser
    with axerve_url in final_url when successful up to hosted checkout.
    """
    try:
        assert_time_left(job.deadline_at, need_seconds=20)
    except TimeoutError as exc:
        return PayResult(False, "http", str(exc))

    timeout = min(
        float(cfg.http_attempt_timeout_seconds),
        max(10.0, seconds_remaining(job.deadline_at) - 15),
    )
    try:
        ax = open_axerve_from_payment_url(
            payment_url=job.payment_url,
            custref=job.custref,
            shop=job.shop or "CV0",
            user_agent=job.user_agent,
            timeout=timeout,
            session=http_session,
        )
        # Intentionally stop here: submit_payment fulfillFlow not fully captured.
        return PayResult(
            False,
            "http",
            "HTTP 已到达 Axerve 托管页；填卡提交走 Drission（fulfill API 未完整抓包）",
            final_url=ax.axerve_url,
            raw_hint="needs_browser",
        )
    except Exception as exc:
        logger.warning("[http_pay] failed: %s", exc)
        return PayResult(
            False,
            "http",
            f"http gateway failed: {exc}",
            final_url=job.payment_url,
            raw_hint="needs_browser",
        )
