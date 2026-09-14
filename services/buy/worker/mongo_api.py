"""Mongo HTTP API client for dingstest (same stack as cenacolo_vivaticket)."""

from __future__ import annotations

from typing import Any

import requests

MONGO_HTTP_BASE = "https://dingstest.133.cn/mongo_api"
MONGO_HTTP_PROXY = ""

ORDERS_COLLECTION = "cenacolo_orders"
RUNS_COLLECTION = "cenacolo_order_runs"
ACCOUNTS_COLLECTION = "vivaticket_accounts"


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
            "User-Agent": "cenacolo-buy-worker/1.0",
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


def raw_query(collection: str, payload: dict[str, Any]) -> Any:
    return _post(f"raw-query/{collection}", payload)


def matched_count(data: Any) -> int:
    if not isinstance(data, dict):
        return 0
    if "matchedCount" in data:
        return int(data["matchedCount"] or 0)
    inner = data.get("result") or data.get("data")
    if isinstance(inner, dict) and "matchedCount" in inner:
        return int(inner["matchedCount"] or 0)
    return 0


class HttpStore:
    def find(
        self,
        collection: str,
        query: dict[str, Any] | None = None,
        *,
        limit: int = 100,
        skip: int = 0,
        sort: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"find": query or {}, "limit": limit, "skip": skip}
        if sort:
            payload["sort"] = sort
        data = raw_query(collection, payload)
        if isinstance(data, dict):
            return list(data.get("documents") or [])
        return []

    def insert(self, collection: str, document: dict[str, Any]) -> str:
        data = _post(f"collections/{collection}", document)
        return str(data.get("id") or "")

    def update(
        self,
        collection: str,
        filter_query: dict[str, Any],
        set_fields: dict[str, Any],
    ) -> int:
        payload = {
            "update": {
                "filter": filter_query,
                "update": {"$set": set_fields},
                "upsert": False,
                "multi": False,
            }
        }
        return matched_count(raw_query(collection, payload))
