from __future__ import annotations

NO_INVENTORY_MARKERS = ("当前无可用票", "NO_INVENTORY")
NO_ACCOUNT = "NO_ACCOUNT"


def is_no_inventory(exc: BaseException) -> bool:
    text = str(exc)
    return any(m in text for m in NO_INVENTORY_MARKERS)
