"""Vivaticket account pool on top of email_163 + vivaticket_accounts."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import Any

from mongo_api import (
    EMAIL_163_COLLECTION,
    FORWARD_ONLY_EMAILS,
    FORWARD_ONLY_ROLE,
    VIVATICKET_ACCOUNTS_COLLECTION,
    mongo_find,
    mongo_find_one,
    mongo_insert,
    mongo_update,
)

SITE = "cenacolovinciano"


def generate_password(length: int = 12) -> str:
    """Random password >= 8 chars (Vivaticket requirement)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


def _used_emails() -> set[str]:
    """Emails already in vivaticket_accounts (any status)."""
    rows = mongo_find(VIVATICKET_ACCOUNTS_COLLECTION, {}, limit=10000)
    return {str(r.get("email", "")).lower() for r in rows if r.get("email")}


def list_accounts(status: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"site": SITE}
    if status:
        query["status"] = status
    return mongo_find(VIVATICKET_ACCOUNTS_COLLECTION, query, limit=10000)


def count_available_mailboxes() -> dict[str, int]:
    """How many email_163 left that are not yet in vivaticket_accounts."""
    used = _used_emails()
    exclude = sorted(used | {e.lower() for e in FORWARD_ONLY_EMAILS})
    idle = mongo_find(
        EMAIL_163_COLLECTION,
        {
            "status": 1,
            "role": {"$ne": FORWARD_ONLY_ROLE},
            "vivaticket_claimed": {"$ne": True},
            "code": {"$exists": True, "$nin": ["", None]},
            "email": {"$nin": exclude} if exclude else {"$exists": True},
        },
        limit=10000,
    )
    any_left = mongo_find(
        EMAIL_163_COLLECTION,
        {
            "role": {"$ne": FORWARD_ONLY_ROLE},
            "vivaticket_claimed": {"$ne": True},
            "code": {"$exists": True, "$nin": ["", None]},
            "email": {"$nin": exclude} if exclude else {"$exists": True},
        },
        limit=10000,
    )
    return {
        "vivaticket_accounts": len(used),
        "email_163_idle_status1": len(idle),
        "email_163_unclaimed_total": len(any_left),
    }


def claim_mailbox() -> dict[str, Any]:
    """
    Take one unused 163 mailbox from email_163 and create a pending
    vivaticket_accounts row (does NOT mark email_163 status=0).
    Skips: already in vivaticket_accounts / forward-only / vivaticket_claimed.
    """
    used = _used_emails()
    exclude = sorted(used | {e.lower() for e in FORWARD_ONLY_EMAILS})

    query: dict[str, Any] = {
        "status": 1,
        "role": {"$ne": FORWARD_ONLY_ROLE},
        "vivaticket_claimed": {"$ne": True},
        "code": {"$exists": True, "$nin": ["", None]},
    }
    if exclude:
        query["email"] = {"$nin": exclude}

    email_doc = mongo_find_one(EMAIL_163_COLLECTION, query)
    if not email_doc:
        query2: dict[str, Any] = {
            "role": {"$ne": FORWARD_ONLY_ROLE},
            "vivaticket_claimed": {"$ne": True},
            "code": {"$exists": True, "$nin": ["", None]},
        }
        if exclude:
            query2["email"] = {"$nin": exclude}
        email_doc = mongo_find_one(EMAIL_163_COLLECTION, query2)
    if not email_doc:
        raise RuntimeError("No available email_163 mailbox for vivaticket pool")

    email_addr = email_doc["email"]
    imap_code = (email_doc.get("code") or "").strip()
    if not imap_code:
        raise RuntimeError(f"email_163 missing code: {email_addr}")

    password = generate_password()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc = {
        "email": email_addr,
        "password": password,
        "imap_code": imap_code,
        "phone_number": email_doc.get("phone_number"),
        "site": SITE,
        "status": "pending",  # pending -> registered -> ready | failed | exists | used
        "created_at": now,
        "updated_at": now,
        "activated_at": None,
        "used_at": None,
        "last_error": "",
        "source_email_163_id": str(email_doc.get("_id") or ""),
    }
    mongo_insert(VIVATICKET_ACCOUNTS_COLLECTION, doc)
    try:
        mongo_update(
            EMAIL_163_COLLECTION,
            {"email": email_addr},
            {"vivaticket_claimed": True, "vivaticket_claimed_at": now},
        )
    except Exception:
        pass
    return doc


def update_account(email: str, fields: dict[str, Any]) -> None:
    fields = {**fields, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    mongo_update(VIVATICKET_ACCOUNTS_COLLECTION, {"email": email}, fields)


def get_account(email: str) -> dict[str, Any] | None:
    return mongo_find_one(VIVATICKET_ACCOUNTS_COLLECTION, {"email": email, "site": SITE})


def claim_ready_account() -> dict[str, Any] | None:
    """Take one ready account for purchase use (does not mark used yet)."""
    return mongo_find_one(
        VIVATICKET_ACCOUNTS_COLLECTION,
        {"site": SITE, "status": "ready"},
    )


def mark_used(email: str, note: str = "") -> None:
    """After ticket purchase (or consume), mark account as used."""
    fields: dict[str, Any] = {
        "status": "used",
        "used_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if note:
        fields["last_error"] = note
    update_account(email, fields)
