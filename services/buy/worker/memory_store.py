"""In-memory Mongo stand-in for worker tests (no HTTP)."""

from __future__ import annotations

import copy
from typing import Any
from uuid import uuid4


def _get_path(doc: dict[str, Any], key: str) -> Any:
    cur: Any = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _path_exists(doc: dict[str, Any], key: str) -> bool:
    cur: Any = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _match_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and any(str(k).startswith("$") for k in expected):
        for op, val in expected.items():
            if op == "$in":
                if actual not in val:
                    return False
            elif op == "$ne":
                if actual == val:
                    return False
            elif op == "$lte":
                if actual is None or actual > val:
                    return False
            elif op == "$gte":
                if actual is None or actual < val:
                    return False
            elif op == "$lt":
                if actual is None or actual >= val:
                    return False
            elif op == "$gt":
                if actual is None or actual <= val:
                    return False
            elif op == "$exists":
                continue
            else:
                return False
        return True
    return actual == expected


def matches(doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if not query:
        return True
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(doc, sub) for sub in expected):
                return False
            continue
        if isinstance(expected, dict) and "$exists" in expected:
            exists = _path_exists(doc, key)
            if bool(expected["$exists"]) != exists:
                return False
            rest = {k: v for k, v in expected.items() if k != "$exists"}
            if rest and not _match_value(_get_path(doc, key), rest):
                return False
            continue
        if not _match_value(_get_path(doc, key), expected):
            return False
    return True


def apply_set(doc: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    for path, value in fields.items():
        parts = path.split(".")
        cur = out
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value
    return out


class MemoryStore:
    def __init__(self) -> None:
        self._cols: dict[str, list[dict[str, Any]]] = {}

    def find(
        self,
        collection: str,
        query: dict[str, Any] | None = None,
        *,
        limit: int = 100,
        skip: int = 0,
        sort: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [copy.deepcopy(d) for d in self._cols.get(collection, []) if matches(d, query)]
        if sort:
            for key, direction in reversed(list(sort.items())):
                rows.sort(
                    key=lambda d, k=key: (_get_path(d, k) is None, _get_path(d, k) or ""),
                    reverse=int(direction) < 0,
                )
        if skip:
            rows = rows[skip:]
        return rows[:limit]

    def insert(self, collection: str, document: dict[str, Any]) -> str:
        doc = copy.deepcopy(document)
        if "_id" not in doc:
            doc["_id"] = uuid4().hex
        self._cols.setdefault(collection, []).append(doc)
        return str(doc["_id"])

    def update(
        self,
        collection: str,
        filter_query: dict[str, Any],
        set_fields: dict[str, Any],
    ) -> int:
        rows = self._cols.setdefault(collection, [])
        for i, doc in enumerate(rows):
            if matches(doc, filter_query):
                rows[i] = apply_set(doc, set_fields)
                return 1
        return 0
