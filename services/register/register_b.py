#!/usr/bin/env python3
"""
Vivaticket Cenacolo member registration — Scheme B PoC.

Flow:
  1) Playwright opens /en/member and passes Incapsula / Safetynet
  2) Export cookies (+ UA) to httpx
  3) GET page → extract fresh keyreg → POST light registration
  4) IMAP poll 163 mailbox for Vivaticket activation mail
  5) HTTP GET activation link (reuse WAF cookies)
  6) Optional login check

Usage:
  python register_b.py --dry-run
  python register_b.py --email you@163.com --password 'YourPass123' --imap-password 'AUTH_CODE'
  python register_b.py --email you@163.com --password 'YourPass123' --imap-password 'AUTH_CODE' --activate-only
"""

from __future__ import annotations

import argparse
import email as email_lib
import html as html_lib
import imaplib
import os
import quopri
import re
import sys
import time
from dataclasses import dataclass
from email.header import decode_header
from typing import Any
from urllib.parse import unquote, urljoin

import httpx
from playwright.sync_api import sync_playwright

MEMBER_URL = "https://cenacolovinciano.vivaticket.it/en/member"
REGISTER_ACTION = "https://cenacolovinciano.vivaticket.it/index.php?nvpg[member]"
LOGIN_ACTION = "https://cenacolovinciano.vivaticket.it/index.php?nvpg[member]"

# Consent IDs observed on cenacolovinciano.vivaticket.it/en/member (may change).
CONSENT_ACK_VIVATICKET = "1652"
CONSENT_ACK_ORGANIZER = "1315"
CONSENT_MARKETING_IDS = ("1650", "1651", "1316", "1317", "1318", "1319")

ACTIVATION_HREF_RE = re.compile(
    r'href=["\']([^"\']*(?:page=attiva|/attiva/|attiv)[^"\']*)["\']',
    re.I,
)
ACTIVATION_URL_RE = re.compile(
    r'https?://[^\s<>"\']+?(?:page=attiva|/attiva/|activation)[^\s<>"\']*',
    re.I,
)


@dataclass
class SessionBundle:
    cookies: dict[str, str]
    user_agent: str
    page_url: str


def _is_incapsula(html: str) -> bool:
    return "Incapsula" in html and len(html) < 1000


def _extract_keyreg(html: str) -> str | None:
    m = re.search(
        r'name=["\']keyreg["\'][^>]*value=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.search(
        r'value=["\']([^"\']+)["\'][^>]*name=["\']keyreg["\']',
        html,
        re.I,
    )
    return m.group(1) if m else None


def _extract_keylogin(html: str) -> str | None:
    m = re.search(
        r'name=["\']keylogin["\'][^>]*value=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.search(
        r'value=["\']([^"\']+)["\'][^>]*name=["\']keylogin["\']',
        html,
        re.I,
    )
    return m.group(1) if m else None


def _extract_hidden(html: str, name: str) -> str | None:
    m = re.search(
        rf'name=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']*)["\']',
        html,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.search(
        rf'value=["\']([^"\']*)["\'][^>]*name=["\']{re.escape(name)}["\']',
        html,
        re.I,
    )
    return m.group(1) if m else None


def _client_headers(session: SessionBundle) -> dict[str, str]:
    return {
        "User-Agent": session.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Origin": "https://cenacolovinciano.vivaticket.it",
        "Referer": session.page_url or MEMBER_URL,
    }


def warmup_session(headless: bool = True, timeout_ms: int = 90_000) -> SessionBundle:
    """Browser-only step: pass WAF and return cookie jar for HTTP replay."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="en-GB")
        page = context.new_page()
        page.goto(MEMBER_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        # Login form may exist but stay hidden in a modal — wait attached, not visible.
        page.wait_for_selector("#signUp #email", state="attached", timeout=timeout_ms)
        for label in ("I agree", "Accept", "Agree"):
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            try:
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=2000)
                    break
            except Exception:
                pass

        light = page.locator("#lightOrFull_L")
        if light.count():
            light.check(force=True)

        cookies = {c["name"]: c["value"] for c in context.cookies()}
        ua = page.evaluate("() => navigator.userAgent")
        page_url = page.url
        browser.close()

    if "reese84" not in cookies and not any(k.startswith("incap_ses") for k in cookies):
        raise RuntimeError("WAF cookies missing after warmup (no reese84 / incap_ses)")

    return SessionBundle(cookies=cookies, user_agent=ua, page_url=page_url)


def build_register_payload(
    *,
    html: str,
    email: str,
    password: str,
    marketing_agree: bool = False,
) -> dict[str, str]:
    keyreg = _extract_keyreg(html)
    if not keyreg:
        raise RuntimeError("keyreg not found on member page HTML")

    dh = _extract_hidden(html, "dh") or "-1,2075"
    sorgente = _extract_hidden(html, "sorgente") or "/en/member"
    reg_type = _extract_hidden(html, "regType") or "l"

    payload: dict[str, str] = {
        "regType": reg_type,
        "sorgente": sorgente,
        "keyreg": keyreg,
        "email": email,
        "password": password,
        "password2": password,
        "dh": dh,
        f"consents[{CONSENT_ACK_VIVATICKET}]": "1",
        f"consents[{CONSENT_ACK_ORGANIZER}]": "1",
    }
    choice = "1" if marketing_agree else "0"
    for cid in CONSENT_MARKETING_IDS:
        payload[f"consents[{cid}]"] = choice

    payload["nazione_nascita"] = "it"
    payload["nazione"] = "it"
    return payload


def classify_register_response(html: str) -> dict[str, Any]:
    low = html.lower()
    incap = _is_incapsula(html)
    activation_hints = [
        "activation",
        "attivazione",
        "confirm your",
        "conferma",
        "check your e-mail",
        "check your email",
        "sent to the e-mail",
        "inviata",
    ]
    error_hints = [
        "already",
        "esistente",
        "already registered",
        "please provide",
        "obbligatori",
        "mandatory",
        "invalid",
        "error",
        "not active",
    ]
    already = "already in use" in low or "already registered" in low or "già in uso" in low
    return {
        "incapsula": incap,
        "len": len(html),
        "has_signup_form": bool(re.search(r'id=["\']signUp["\']', html)),
        "maybe_activation": any(h in low for h in activation_hints) and not incap,
        "maybe_error": any(h in low for h in error_hints),
        "already_registered": already,
        "title": (re.search(r"<title[^>]*>([^<]*)", html, re.I) or [None, None])[1],
    }


def http_register(
    session: SessionBundle,
    *,
    email: str,
    password: str,
    dry_run: bool = False,
    marketing_agree: bool = False,
) -> dict[str, Any]:
    headers = _client_headers(session)

    with httpx.Client(
        headers=headers,
        cookies=session.cookies,
        follow_redirects=True,
        timeout=45.0,
    ) as client:
        r_get = client.get(MEMBER_URL)
        if _is_incapsula(r_get.text):
            return {
                "ok": False,
                "stage": "get",
                "reason": "incapsula_on_get",
                "status": r_get.status_code,
                "classify": classify_register_response(r_get.text),
            }

        if dry_run:
            payload = build_register_payload(
                html=r_get.text,
                email="dry-run-invalid",
                password="short",
                marketing_agree=False,
            )
        else:
            payload = build_register_payload(
                html=r_get.text,
                email=email,
                password=password,
                marketing_agree=marketing_agree,
            )

        r_post = client.post(
            REGISTER_ACTION,
            data=payload,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        # Keep cookies refreshed for later activate/login.
        session.cookies.update({c.name: c.value for c in client.cookies.jar})
        cls = classify_register_response(r_post.text)
        ok = (not cls["incapsula"]) and (
            cls["maybe_activation"]
            or cls["already_registered"]
            or (dry_run and cls["maybe_error"] and cls["has_signup_form"])
            or (not dry_run and not cls["has_signup_form"])
        )
        return {
            "ok": ok,
            "stage": "post",
            "dry_run": dry_run,
            "status": r_post.status_code,
            "final_url": str(r_post.url),
            "classify": cls,
            "keyreg_prefix": payload.get("keyreg", "")[:16],
            "snippet": re.sub(r"\s+", " ", r_post.text)[:400],
        }


# ---------- IMAP activation ----------


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", "ignore"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _msg_body_text(msg: email_lib.message.Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            raw = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                chunks.append(raw.decode(charset, "ignore"))
            except Exception:
                chunks.append(raw.decode("utf-8", "ignore"))
    else:
        raw = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            chunks.append(raw.decode(charset, "ignore"))
        except Exception:
            chunks.append(raw.decode("utf-8", "ignore"))
    body = "\n".join(chunks)
    # Some gateways leave QP artefacts even after decode.
    if "=3D" in body or "=\n" in body:
        try:
            body = quopri.decodestring(body.encode("utf-8", "ignore")).decode("utf-8", "ignore")
        except Exception:
            pass
    return body


def extract_activation_link(body: str) -> str | None:
    """Pull the real activation href from Vivaticket mail HTML/text."""
    body = html_lib.unescape(body)
    candidates: list[str] = []
    for m in ACTIVATION_HREF_RE.finditer(body):
        candidates.append(m.group(1))
    for m in ACTIVATION_URL_RE.finditer(body):
        candidates.append(m.group(0))

    cleaned: list[str] = []
    for c in candidates:
        url = html_lib.unescape(c.strip().rstrip(").,;\"'"))
        url = unquote(url)
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            url = urljoin("https://cenacolovinciano.vivaticket.it/", url)
        if "page=attiva" in url or "attiva" in url.lower() or "activation" in url.lower():
            # Prefer cenacolo / vivaticket hosts
            if "vivaticket" in url:
                cleaned.append(url)

    if not cleaned:
        return None

    # Prefer links that still carry a non-trivial token (aU=...).
    def score(u: str) -> tuple[int, int]:
        au = re.search(r"[?&]aU=([^&]+)", u)
        token = au.group(1) if au else ""
        return (1 if token not in ("", "-2", "0") else 0, len(token))

    cleaned.sort(key=score, reverse=True)
    return cleaned[0]


class ImapAuthError(RuntimeError):
    """163 IMAP auth code invalid / login rejected."""


def _imap_connect(email_addr: str, imap_password: str) -> imaplib.IMAP4_SSL:
    if email_addr.lower().endswith("@163.com"):
        host = "imap.163.com"
    else:
        raise ValueError(f"Unsupported mailbox domain for IMAP PoC: {email_addr}")

    imaplib.Commands["ID"] = ("AUTH",)
    mail = imaplib.IMAP4_SSL(host, 993)
    try:
        mail.login(email_addr, imap_password)
    except Exception as e:
        msg = str(e).lower()
        raw = repr(e).lower()
        if "login" in msg or "password" in msg or "authentication" in msg or "login" in raw:
            raise ImapAuthError(f"IMAP login failed for {email_addr}: {e}") from e
        raise
    id_pairs = (
        '"name" "vivaticket-poc"',
        '"version" "1.0.0"',
        '"vendor" "cenacolo_vivaticket"',
        f'"support-email" "{email_addr}"',
    )
    mail._simple_command("ID", "(" + " ".join(id_pairs) + ")")
    return mail


def verify_imap_login(email_addr: str, imap_password: str) -> None:
    """Quick IMAP auth check before registering on Vivaticket."""
    mail = _imap_connect(email_addr, imap_password)
    try:
        status, _ = mail.select("INBOX")
        if status != "OK":
            raise RuntimeError(f"IMAP INBOX select failed for {email_addr}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_activation_link_imap(
    *,
    email_addr: str,
    imap_password: str,
    timeout_sec: int = 120,
    poll_interval: float = 5.0,
    lookback: int = 30,
) -> str:
    """Poll INBOX for Vivaticket activation mail and return activation URL."""
    deadline = time.time() + timeout_sec
    last_err: Exception | None = None

    while time.time() < deadline:
        mail = None
        try:
            mail = _imap_connect(email_addr, imap_password)
            status, _ = mail.select("INBOX")
            if status != "OK":
                raise RuntimeError("IMAP select INBOX failed")

            status, messages = mail.search(None, "ALL")
            if status != "OK":
                raise RuntimeError("IMAP search failed")

            ids = messages[0].split()
            for mail_id in reversed(ids[-lookback:]):
                status, data = mail.fetch(mail_id, "(RFC822)")
                if status != "OK" or not data or not data[0]:
                    continue
                raw = data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                msg = email_lib.message_from_bytes(raw)
                subject = _decode_header_value(msg.get("Subject"))
                from_ = _decode_header_value(msg.get("From"))
                from_l = from_.lower()
                subj_l = subject.lower()
                if "vivaticket" not in from_l and "vivaticket" not in subj_l:
                    continue
                body = _msg_body_text(msg)
                link = extract_activation_link(body)
                if link:
                    mail.logout()
                    return link
        except ImapAuthError:
            raise
        except Exception as e:
            last_err = e
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Activation mail not found within {timeout_sec}s"
        + (f" (last error: {last_err})" if last_err else "")
    )


def http_activate(session: SessionBundle, activation_url: str) -> dict[str, Any]:
    headers = _client_headers(session)
    with httpx.Client(
        headers=headers,
        cookies=session.cookies,
        follow_redirects=True,
        timeout=45.0,
    ) as client:
        resp = client.get(activation_url)
        session.cookies.update({c.name: c.value for c in client.cookies.jar})
        text = resp.text
        low = text.lower()
        incap = _is_incapsula(text)
        success_hints = [
            "activated",
            "activation successful",
            "attivato",
            "attiva",
            "success",
            "complet",
            "已激活",
            "激活成功",
            "account is now",
            "you can now",
            "login",
        ]
        fail_hints = ["invalid", "expired", "error", "not valid", "scadut"]
        return {
            "ok": (not incap) and any(h in low for h in success_hints),
            "incapsula": incap,
            "status": resp.status_code,
            "final_url": str(resp.url),
            "len": len(text),
            "maybe_fail": any(h in low for h in fail_hints),
            "snippet": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:350],
        }


def http_login(session: SessionBundle, *, email: str, password: str) -> dict[str, Any]:
    headers = _client_headers(session)
    with httpx.Client(
        headers=headers,
        cookies=session.cookies,
        follow_redirects=True,
        timeout=45.0,
    ) as client:
        r_get = client.get(MEMBER_URL)
        if _is_incapsula(r_get.text):
            return {"ok": False, "reason": "incapsula_on_get", "status": r_get.status_code}

        keylogin = _extract_keylogin(r_get.text)
        if not keylogin:
            return {"ok": False, "reason": "keylogin_missing"}

        payload = {
            "sorgente": "",
            "page": "login",
            "keylogin": keylogin,
            "unam": email,
            "upwd": password,
        }
        resp = client.post(
            LOGIN_ACTION,
            data=payload,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        session.cookies.update({c.name: c.value for c in client.cookies.jar})
        low = resp.text.lower()
        bad = (
            "password is wrong" in low
            or "not active" in low
            or "username and/or password" in low
            or "user is not active" in low
        )
        # Heuristic: logged-in pages usually drop the signup form / show account area.
        logged_in = (not bad) and (
            "sign out" in low
            or "logout" in low
            or "my purchases" in low
            or "my details" in low
            or ("my vivaticket" in low and "username or password is wrong" not in low)
        )
        return {
            "ok": logged_in,
            "bad_credentials_or_inactive": bad,
            "status": resp.status_code,
            "final_url": str(resp.url),
            "len": len(resp.text),
            "snippet": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.text))[:350],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vivaticket register PoC (scheme B)")
    parser.add_argument("--email", help="Account email")
    parser.add_argument("--password", help="Account password (min 8 chars)")
    parser.add_argument(
        "--imap-password",
        default=os.environ.get("VIVATICKET_IMAP_PASSWORD", ""),
        help="163 IMAP auth code (or env VIVATICKET_IMAP_PASSWORD)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Probe only; no real account")
    parser.add_argument(
        "--activate-only",
        action="store_true",
        help="Skip register; IMAP + activate + login only",
    )
    parser.add_argument("--skip-activate", action="store_true", help="Register only, no IMAP")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--marketing", action="store_true", help="Opt-in marketing consents")
    parser.add_argument("--imap-timeout", type=int, default=180, help="Seconds to wait for mail")
    parser.add_argument("--keep-warmup-ms", type=int, default=0, help="Pause after open (debug)")
    args = parser.parse_args(argv)

    if args.dry_run:
        need_creds = False
    elif args.activate_only:
        need_creds = True
    else:
        need_creds = True

    if need_creds and (not args.email or not args.password):
        parser.error("Provide --email and --password")

    if not args.dry_run and not args.skip_activate and not args.imap_password:
        parser.error("Provide --imap-password (163 auth code) or --skip-activate")

    if not args.dry_run and args.password and len(args.password) < 8:
        parser.error("Password must be at least 8 characters")

    print("[1] Browser warmup (WAF / Safetynet)...", flush=True)
    t0 = time.time()
    session = warmup_session(headless=not args.headed)
    print(
        f"    cookies={len(session.cookies)} "
        f"reese84={'reese84' in session.cookies} "
        f"incap={[k for k in session.cookies if k.startswith('incap_ses')]} "
        f"elapsed={time.time()-t0:.1f}s",
        flush=True,
    )
    if args.keep_warmup_ms:
        time.sleep(args.keep_warmup_ms / 1000)

    if args.dry_run:
        print("[2] HTTP dry-run register...", flush=True)
        result = http_register(
            session,
            email="",
            password="",
            dry_run=True,
            marketing_agree=False,
        )
        print(result, flush=True)
        if result.get("ok"):
            print("DRY-RUN OK: session reusable; app-layer validation reached.")
            return 0
        print("FAILED:", result.get("reason") or result.get("classify"), file=sys.stderr)
        return 1

    if not args.activate_only:
        print("[2] HTTP register...", flush=True)
        result = http_register(
            session,
            email=args.email,
            password=args.password,
            dry_run=False,
            marketing_agree=args.marketing,
        )
        print(result, flush=True)
        if not result.get("ok"):
            print("FAILED register:", result.get("reason") or result.get("classify"), file=sys.stderr)
            return 1
        if result.get("classify", {}).get("already_registered"):
            print("    note: email already registered — continuing to activate/login", flush=True)
        else:
            print("    register submitted", flush=True)
    else:
        print("[2] Skip register (--activate-only)", flush=True)

    if args.skip_activate:
        print("Done (skipped activate).")
        return 0

    print("[3] IMAP fetch activation link...", flush=True)
    link = fetch_activation_link_imap(
        email_addr=args.email,
        imap_password=args.imap_password,
        timeout_sec=args.imap_timeout,
    )
    # Do not print full tokenized URL in logs if avoidable; show host+path only.
    safe = re.sub(r"(aU=)[^&]+", r"\1***", link)
    print(f"    link={safe}", flush=True)

    print("[4] HTTP activate...", flush=True)
    act = http_activate(session, link)
    print(act, flush=True)
    if act.get("incapsula"):
        print("    WAF blocked activate; refreshing session and retrying once...", flush=True)
        session = warmup_session(headless=not args.headed)
        act = http_activate(session, link)
        print(act, flush=True)

    print("[5] HTTP login check...", flush=True)
    login = http_login(session, email=args.email, password=args.password)
    print(login, flush=True)

    if login.get("ok"):
        print("SUCCESS: account activated and login OK.")
        return 0

    if act.get("ok") and login.get("bad_credentials_or_inactive"):
        print(
            "Activation request sent, but login still inactive/wrong. "
            "Re-check mail link or password.",
            file=sys.stderr,
        )
        return 2

    if login.get("bad_credentials_or_inactive"):
        print("Login failed: wrong password or still not active.", file=sys.stderr)
        return 2

    # Soft success if activate page looked good even when login heuristic is weak.
    if act.get("ok") and not act.get("incapsula"):
        print("Activation likely OK; login heuristic inconclusive — verify manually.")
        return 0

    print("FAILED activate/login flow.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
