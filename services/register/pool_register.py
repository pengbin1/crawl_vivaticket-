#!/usr/bin/env python3
"""
Claim 163 mailboxes from email_163, register+activate on Vivaticket (scheme B),
store credentials in vivaticket_accounts.

Usage:
  python pool_register.py --available
  python pool_register.py --count 200
  python pool_register.py --list
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback

from account_pool import (
    claim_mailbox,
    count_available_mailboxes,
    list_accounts,
    update_account,
)
from register_b import (
    ImapAuthError,
    fetch_activation_link_imap,
    http_activate,
    http_login,
    http_register,
    verify_imap_login,
    warmup_session,
)


def register_one(*, headed: bool = False, imap_timeout: int = 180) -> dict:
    account = claim_mailbox()
    email = account["email"]
    password = account["password"]
    imap_code = account["imap_code"]
    print(f"==> claimed {email}", flush=True)

    try:
        # Fail fast: bad IMAP auth must not register on Vivaticket
        print("    check IMAP login...", flush=True)
        try:
            verify_imap_login(email, imap_code)
        except ImapAuthError as e:
            update_account(
                email,
                {"status": "failed", "last_error": f"imap_auth:{e}"},
            )
            print(f"    skip: IMAP auth failed -> {email}", flush=True)
            return {
                "email": email,
                "ok": False,
                "skipped": True,
                "stage": "imap_auth",
                "error": str(e),
            }

        session = warmup_session(headless=not headed)
        reg = http_register(
            session,
            email=email,
            password=password,
            dry_run=False,
            marketing_agree=False,
        )
        print("register:", {k: reg[k] for k in ("ok", "status", "classify") if k in reg}, flush=True)
        if not reg.get("ok"):
            update_account(
                email,
                {"status": "failed", "last_error": f"register_failed:{reg}"},
            )
            return {"email": email, "ok": False, "stage": "register", "detail": reg}

        # Already on Vivaticket with unknown password → skip, do not activate
        if reg.get("classify", {}).get("already_registered"):
            update_account(
                email,
                {
                    "status": "exists",
                    "last_error": "already_registered_on_vivaticket",
                },
            )
            print(f"    skip: already registered on site -> {email}", flush=True)
            return {
                "email": email,
                "ok": False,
                "skipped": True,
                "stage": "already_registered",
            }

        update_account(email, {"status": "registered", "last_error": ""})

        link = fetch_activation_link_imap(
            email_addr=email,
            imap_password=imap_code,
            timeout_sec=imap_timeout,
        )
        safe_link = link.split("?")[0]
        print(f"activation link host/path: {safe_link}", flush=True)

        act = http_activate(session, link)
        if act.get("incapsula"):
            session = warmup_session(headless=not headed)
            act = http_activate(session, link)
        print(
            "activate:",
            {k: act[k] for k in ("ok", "status", "incapsula", "final_url") if k in act},
            flush=True,
        )

        login = http_login(session, email=email, password=password)
        print(
            "login:",
            {k: login[k] for k in ("ok", "bad_credentials_or_inactive", "status") if k in login},
            flush=True,
        )

        if login.get("ok") or (act.get("ok") and not act.get("incapsula")):
            update_account(
                email,
                {
                    "status": "ready",
                    "activated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_error": "" if login.get("ok") else "login_heuristic_weak",
                },
            )
            return {
                "email": email,
                "password": password,
                "ok": True,
                "login_ok": bool(login.get("ok")),
                "status": "ready",
            }

        update_account(
            email,
            {
                "status": "failed",
                "last_error": f"activate_or_login_failed act={act} login={login}",
            },
        )
        return {"email": email, "ok": False, "stage": "activate_login", "act": act, "login": login}

    except ImapAuthError as e:
        update_account(email, {"status": "failed", "last_error": f"imap_auth:{e}"})
        print(f"    skip: IMAP auth failed -> {email}", flush=True)
        return {"email": email, "ok": False, "skipped": True, "stage": "imap_auth", "error": str(e)}
    except Exception as e:
        traceback.print_exc()
        update_account(email, {"status": "failed", "last_error": str(e)})
        return {"email": email, "ok": False, "stage": "exception", "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch register Vivaticket accounts into Mongo pool")
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Target number of SUCCESSFUL ready accounts (skips already-registered)",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--imap-timeout", type=int, default=180)
    parser.add_argument("--list", action="store_true", help="List vivaticket_accounts and exit")
    parser.add_argument(
        "--available",
        action="store_true",
        help="Show how many email_163 left unclaimed",
    )
    parser.add_argument(
        "--mark-used",
        metavar="EMAIL",
        help="Mark an account as used after purchase",
    )
    parser.add_argument("--sleep", type=float, default=2.0, help="Pause between attempts")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="Max try times (default: count*3, for skips/failures)",
    )
    args = parser.parse_args(argv)

    from logutil import setup_logging

    setup_logging(service="register")

    if args.list:
        rows = list_accounts()
        print(f"count={len(rows)}")
        for r in rows:
            pwd = str(r.get("password") or "")
            masked = (pwd[:2] + "***") if pwd else ""
            print(
                f"{r.get('status')}\t{r.get('email')}\tpassword={masked}\t"
                f"updated={r.get('updated_at')}\terr={r.get('last_error')}"
            )
        return 0

    if args.available:
        stats = count_available_mailboxes()
        print(stats)
        return 0

    if args.mark_used:
        from account_pool import mark_used

        mark_used(args.mark_used)
        print(f"marked used: {args.mark_used}")
        return 0

    target = args.count
    max_attempts = args.max_attempts or max(target * 3, target + 20)
    results = []
    ok_n = 0
    attempt = 0

    print(f"target_success={target} max_attempts={max_attempts}", flush=True)
    print("available:", count_available_mailboxes(), flush=True)

    while ok_n < target and attempt < max_attempts:
        attempt += 1
        print(f"\n===== attempt {attempt}/{max_attempts}  success {ok_n}/{target} =====", flush=True)
        try:
            r = register_one(headed=args.headed, imap_timeout=args.imap_timeout)
        except RuntimeError as e:
            print(f"STOP: {e}", flush=True)
            break
        results.append(r)
        if r.get("ok"):
            ok_n += 1
        if ok_n < target and args.sleep > 0:
            time.sleep(args.sleep)

    print(f"\nDone: {ok_n}/{target} ready (attempts={attempt})")
    for r in results:
        print(r)
    return 0 if ok_n >= target else 1


if __name__ == "__main__":
    raise SystemExit(main())
