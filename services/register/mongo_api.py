"""Mongo HTTP API — hardcoded for personal deploy (same as hotpepper / pokemon).

Uses dingstest HTTP API only (no pymongo direct).
"""

from __future__ import annotations

from typing import Any

import requests

# --- hardcoded (same stack as hotpepper_gourmet_crawler) ---
MONGO_HTTP_BASE = "https://dingstest.133.cn/mongo_api"
# 日本机与 hotpepper 一致：留空直连；若在国内访问 dingstest 改成下面这行代理
# MONGO_HTTP_PROXY = "http://120.133.0.172:4780"
MONGO_HTTP_PROXY = ""

# Source mailbox pool (163 + IMAP auth code)
EMAIL_163_COLLECTION = "email_163"
# Vivaticket account pool
VIVATICKET_ACCOUNTS_COLLECTION = "vivaticket_accounts"

# Same exclusions as pokemon_cafe
FORWARD_ONLY_EMAILS = {"dings_service@163.com"}
FORWARD_ONLY_ROLE = "pkm_forward_service"


def _proxies() -> dict[str, str] | None:
    if not MONGO_HTTP_PROXY:
        return None
    return {"http": MONGO_HTTP_PROXY, "https": MONGO_HTTP_PROXY}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "cenacolo-vivaticket/1.0",
        }
    )
    s.trust_env = False
    return s


def _post(path: str, payload: Any, timeout: int = 30) -> Any:
    url = f"{MONGO_HTTP_BASE}/{path.lstrip('/')}"
    resp = _session().post(url, json=payload, timeout=timeout, proxies=_proxies())
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("msg") == "error":
        raise RuntimeError(f"mongo_api error: {data}")
    return data


def _raw_query(collection: str, payload: dict[str, Any]) -> Any:
    return _post(f"raw-query/{collection}", payload)


def mongo_find(
    collection: str,
    query: dict[str, Any] | None = None,
    *,
    limit: int = 5000,
    skip: int = 0,
) -> list[dict[str, Any]]:
    data = _raw_query(
        collection,
        {"find": query or {}, "limit": limit, "skip": skip},
    )
    if isinstance(data, dict):
        return list(data.get("documents") or [])
    return []


def mongo_find_one(collection: str, query: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = mongo_find(collection, query or {}, limit=1)
    return rows[0] if rows else None


def mongo_insert(collection: str, document: dict[str, Any]) -> str:
    data = _post(f"collections/{collection}", document)
    return str(data.get("id") or "")


def mongo_update(
    collection: str,
    filter_query: dict[str, Any],
    update_query: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "update": {
            "filter": filter_query,
            "update": {"$set": update_query},
            "upsert": False,
            "multi": False,
        }
    }
    return _raw_query(collection, payload)
