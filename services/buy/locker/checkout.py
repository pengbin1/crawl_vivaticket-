from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from lxml import html as lxml_html

from shared.captcha import YesCaptchaClient
from shared.config import AppConfig
from shared.log import get_logger
from shared.models import PaymentJob, TimeSlot, utc_now
from locker.waf import session_cookie_dict

logger = get_logger(__name__)


def extract_js_redirect(html_text: str, base: str) -> str:
    pay_host = "https://secure.vivaticket.com/paycmd/formtr.php"
    m = re.search(
        r"https?://secure\.vivaticket\.com/paycmd/formtr\.php\?[^\s'\"<>]+",
        html_text,
    )
    if m:
        return m.group(0)
    m = re.search(
        r"""(?:document|window)\.location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""",
        html_text,
    )
    if m:
        return urljoin(base, m.group(1))
    m = re.search(r"VIVATK\d+", html_text)
    if m:
        return f"{pay_host}?custref={m.group(0)}&shop=CV0"
    return ""


def extract_custref(payment_url: str) -> str:
    qs = parse_qs(urlparse(payment_url).query)
    vals = qs.get("custref") or []
    if vals:
        return vals[0]
    m = re.search(r"VIVATK\d+", payment_url)
    return m.group(0) if m else ""


def extract_full_fare_choice(price_html: str, slot: TimeSlot) -> dict[str, Any]:
    tree = lxml_html.fromstring(price_html)
    choices = tree.xpath(
        '//*[@data-role="amount" and not(@data-mandatory) and contains(@data-redu, "INTERO / FULL FARE")]'
    )
    if not choices:
        choices = tree.xpath(
            '//*[@data-role="amount" and not(@data-mandatory) and @data-price="1500"]'
        )
    if not choices:
        choices = tree.xpath('//*[@data-role="amount" and not(@data-mandatory)]')
    if not choices:
        raise RuntimeError("价格页没有找到全价票 amount 节点")

    choice = choices[0]
    tcode_match = re.search(r"var\s+tcode\s*=\s*'([^']+)'", price_html)
    pcode_match = re.search(r"var\s+pcode\s*=\s*'([^']+)'", price_html)
    return {
        "tcode": tcode_match.group(1) if tcode_match else slot.tcode,
        "pcode": pcode_match.group(1) if pcode_match else slot.pcode,
        "zone_id": choice.get("data-znid"),
        "zone_name": choice.get("data-key"),
        "rid_id": choice.get("data-ridid"),
        "rid_name": choice.get("data-redu"),
        "price": int(choice.get("data-price") or 0),
        "numbered": choice.get("data-numbered") or "0",
        "max": int(choice.get("data-max") or 0),
    }


def classify_buy_response(html_text: str) -> str:
    lowered = html_text.lower()
    if "seats temporarily not assignable" in lowered:
        return "seat_not_assignable"
    if "form_pd_" in html_text or "ana_firstname" in html_text or "ana_lastname" in html_text:
        return "needs_personal"
    if "formbasket" in lowered or "missing personal data" in lowered:
        return "cart"
    if "data-sitekey" in html_text or "recaptcha/api.js" in lowered:
        return "captcha_failed"
    if "document.location" in lowered and "cmd=prices" in lowered:
        return "redirect_to_prices"
    return "unknown"


def _collect_form_fields(form) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for element in form.xpath(".//input|.//select|.//textarea"):
        name = element.get("name")
        if not name:
            continue
        tag = element.tag.lower()
        if tag == "input":
            input_type = (element.get("type") or "text").lower()
            if input_type in {"submit", "button", "image", "reset", "file"}:
                continue
            if input_type in {"checkbox", "radio"} and "checked" not in element.attrib:
                continue
            fields.append((name, element.get("value", "")))
        elif tag == "textarea":
            fields.append((name, element.text or ""))
        elif tag == "select":
            options = element.xpath("./option[@selected]") or element.xpath("./option[1]")
            for option in options:
                fields.append((name, option.get("value") or option.text or ""))
    return fields


# Observed on live prices / pay.js for cenacolovinciano
KNOWN_RECAPTCHA_SITEKEY = "6Lc04c8qAAAAAMaNadNYHWFiFTBP_UK_FWwq5ys7"


def _doc_headers(session: requests.Session, referer: str, origin: str = "") -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer,
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": session.headers.get("User-Agent", ""),
    }
    if origin:
        headers["Origin"] = origin
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return headers


def extract_sitekey(price_html: str) -> str | None:
    m = re.search(r'data-sitekey=["\']([^"\']+)["\']', price_html, re.I)
    if m:
        return m.group(1)
    m = re.search(r'sitekey["\']?\s*[:=]\s*["\']([^"\']+)["\']', price_html, re.I)
    if m:
        return m.group(1)
    if "g-recaptcha" in price_html or "recaptcha/api.js" in price_html.lower():
        return KNOWN_RECAPTCHA_SITEKEY
    # Some price renders omit the widget markup but still accept the same sitekey.
    if 'data-role="amount"' in price_html or "frmAcquisto" in price_html:
        return KNOWN_RECAPTCHA_SITEKEY
    return None


def _dump_debug(name: str, html_text: str) -> None:
    from shared.log import default_artifact_dir

    path = default_artifact_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    logger.info("[checkout] wrote debug html %s len=%s", path, len(html_text))


def try_lock_slot(
    session: requests.Session,
    cfg: AppConfig,
    slot: TimeSlot,
) -> PaymentJob | None:
    prices_url = (
        f"{cfg.base}/index.php?nvpg[sell]&cmd=prices"
        f"&wms_op=cenacoloVinciano&pcode={slot.pcode}&tcode={slot.tcode}"
    )
    r_prices = session.get(
        f"{cfg.base}/index.php",
        params={
            "nvpg[sell]": "",
            "cmd": "prices",
            "wms_op": "cenacoloVinciano",
            "pcode": slot.pcode,
            "tcode": slot.tcode,
        },
        headers=_doc_headers(session, cfg.event_url),
        timeout=60,
    )
    price_html = r_prices.text
    logger.info(
        "[checkout] prices %s %s status=%s len=%s url=%s",
        slot.date,
        slot.ora,
        r_prices.status_code,
        len(price_html),
        r_prices.url,
    )

    if "Incapsula" in price_html and len(price_html) < 1500:
        _dump_debug("debug_prices_incapsula.html", price_html)
        logger.warning("[checkout] prices hit Incapsula")
        return None

    try:
        choice = extract_full_fare_choice(price_html, slot)
    except Exception as exc:
        _dump_debug(f"debug_prices_{slot.pcode}.html", price_html)
        logger.warning("[checkout] prices page has no fare choice: %s", exc)
        return None

    sitekey = extract_sitekey(price_html)
    if not sitekey:
        _dump_debug(f"debug_prices_nositekey_{slot.pcode}.html", price_html)
        logger.warning("[checkout] no recaptcha sitekey on prices page")
        return None
    logger.info("[checkout] sitekey=%s...", sitekey[:16])
    if not cfg.captcha_key:
        raise RuntimeError("需要 captcha.client_key 才能锁座")

    captcha = YesCaptchaClient(cfg.captcha_key, cfg.captcha_china)
    token = captcha.solve(
        website_url=str(r_prices.url),
        website_key=sitekey,
        timeout=float(cfg.captcha_timeout),
    )
    if not token:
        logger.warning("[checkout] captcha failed")
        return None
    if choice["max"] and cfg.ticket_count > choice["max"]:
        logger.warning("[checkout] ticket_count>%s max", choice["max"])
        return None

    seatstructure = {
        "zones": [],
        "seats": [
            {
                "zoneId": int(choice["zone_id"]),
                "zoneName": choice["zone_name"],
                "ridId": int(choice["rid_id"]),
                "ridName": choice["rid_name"],
                "price": choice["price"],
                "amount": cfg.ticket_count,
                "items": [],
                "id": "",
                "abbo": "",
            }
        ],
        "tcode": choice["tcode"],
        "pcode": choice["pcode"],
    }
    buy_data = [
        ("cmd", "checkAnagrafica"),
        ("nextStep", "tabellaPosti"),
        ("tcode", str(choice["tcode"])),
        ("pcode", str(choice["pcode"])),
        ("zona[0]", str(choice["zone_id"])),
        ("zonaN[0]", str(choice["zone_name"])),
        ("reduction[0]", str(choice["rid_id"])),
        ("nTickets[0]", str(cfg.ticket_count)),
        ("numbered", str(choice["numbered"])),
        ("seatstructure", json.dumps(seatstructure, separators=(",", ":"))),
        ("g-recaptcha-response", token),
    ]
    buy_resp = session.post(
        f"{cfg.base}/index.php?nvpg[sell]",
        data=buy_data,
        headers=_doc_headers(session, r_prices.url, origin=cfg.base),
        timeout=60,
    )
    kind = classify_buy_response(buy_resp.text)
    logger.info("[checkout] lock classify=%s len=%s", kind, len(buy_resp.text))
    if kind in {"seat_not_assignable", "captcha_failed", "redirect_to_prices"}:
        return None

    personal_tree = lxml_html.fromstring(buy_resp.text)
    forms = personal_tree.xpath('//form[starts-with(@id, "form_pd_")]')
    if not forms:
        forms = personal_tree.xpath(
            '//form[.//input[@name="ana_firstname"] or .//input[@name="ana_lastname"]]'
        )
    if not forms:
        # maybe already cart
        if "formBasket" in buy_resp.text or "visualizzaCarrello" in buy_resp.text:
            logger.info("[checkout] no personal forms, try cart path")
        else:
            logger.warning("[checkout] no personal forms")
            return None
    else:
        matched = []
        for form in forms:
            pvals = form.xpath('.//input[@name="pcode"]/@value')
            if pvals and pvals[0] == str(choice["pcode"]):
                matched.append(form)
        if not matched:
            matched = forms[: cfg.ticket_count]
        for idx, form in enumerate(matched[: cfg.ticket_count]):
            passenger = (
                cfg.passengers[idx]
                if idx < len(cfg.passengers)
                else cfg.passengers[-1]
            )
            action = urljoin(
                cfg.base,
                form.get("action") or "/index.php?nvpg[sell]&cmd=visualizzaCarrello",
            )
            fields = _collect_form_fields(form)
            replaced_fn = replaced_ln = False
            for i, (k, v) in enumerate(fields):
                if k == "ana_firstname":
                    fields[i] = ("ana_firstname", passenger.first_name)
                    replaced_fn = True
                elif k == "ana_lastname":
                    fields[i] = ("ana_lastname", passenger.last_name)
                    replaced_ln = True
            if not replaced_fn:
                fields.append(("ana_firstname", passenger.first_name))
            if not replaced_ln:
                fields.append(("ana_lastname", passenger.last_name))
            if not any(k == "subcmd" for k, _ in fields):
                fields.append(("subcmd", "personalDetails"))
            pr = session.post(
                action,
                data=fields,
                headers=_doc_headers(session, buy_resp.url, origin=cfg.base),
                timeout=60,
            )
            logger.info(
                "[checkout] personal #%s status=%s len=%s",
                idx + 1,
                pr.status_code,
                len(pr.text),
            )

    cart_resp = session.get(
        f"{cfg.base}/index.php",
        params={"nvpg[sell]": "", "cmd": "visualizzaCarrello"},
        headers=_doc_headers(session, buy_resp.url),
        timeout=60,
    )
    cart_tree = lxml_html.fromstring(cart_resp.text)
    baskets = cart_tree.xpath('//form[@id="formBasket"]')
    if not baskets:
        logger.warning("[checkout] formBasket missing")
        return None
    blocca_data = _collect_form_fields(baskets[0])
    overrides = {
        "cmd": "bloccaCarrello",
        "data": "",
        "vouchercode": "",
        "sped[prezzo]": "000",
        "donation_value": "0",
        "id_donation": "0",
        "invoiceNeeded": "0",
        "invoiceNewAddress": "1",
        "invoice_type": "p",
        "ft_nazione": "it",
        "checkRegolamento": "on",
        "checkRegolamentoCenacolo": "on",
        "checkRecessoCenacolo": "on",
    }
    # apply overrides
    names = {k for k, _ in blocca_data}
    new_data: list[tuple[str, str]] = []
    for k, v in blocca_data:
        if k in overrides:
            new_data.append((k, overrides[k]))
        else:
            new_data.append((k, v))
    for k, v in overrides.items():
        if k not in names:
            new_data.append((k, v))

    blocca_resp = session.post(
        f"{cfg.base}/index.php?nvpg[sell]",
        data=new_data,
        headers=_doc_headers(session, cart_resp.url, origin=cfg.base),
        timeout=60,
    )
    payment_url = extract_js_redirect(blocca_resp.text, cfg.base)
    if not payment_url:
        # follow meta refresh / location header
        if blocca_resp.history:
            payment_url = extract_js_redirect(blocca_resp.text, cfg.base)
        logger.warning(
            "[checkout] blocca no payment url len=%s", len(blocca_resp.text)
        )
        # still try custref in body
        if not payment_url:
            return None

    locked_at = utc_now()
    deadline_at = locked_at + timedelta(seconds=cfg.lock_ttl_seconds)
    custref = extract_custref(payment_url)
    amount = int(choice["price"]) * cfg.ticket_count
    logger.info(
        "[checkout] LOCKED custref=%s deadline=%s url=%s",
        custref,
        deadline_at.isoformat(),
        payment_url,
    )
    return PaymentJob(
        payment_url=payment_url,
        custref=custref,
        account_email=cfg.email,
        date=slot.date,
        time=slot.ora,
        tcode=slot.tcode,
        pcode=slot.pcode,
        ticket_count=cfg.ticket_count,
        amount_cents=amount,
        passengers=list(cfg.passengers),
        user_agent=str(session.headers.get("User-Agent") or ""),
        locked_at=locked_at,
        deadline_at=deadline_at,
        cookies=session_cookie_dict(session),
    )
