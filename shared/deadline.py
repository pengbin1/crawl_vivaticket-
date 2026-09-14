from __future__ import annotations

from datetime import datetime, timezone


def seconds_remaining(deadline_at: datetime) -> float:
    if deadline_at.tzinfo is None:
        deadline_at = deadline_at.replace(tzinfo=timezone.utc)
    return (deadline_at - datetime.now(timezone.utc)).total_seconds()


def is_urgent(deadline_at: datetime, urgent_remaining_seconds: int) -> bool:
    return seconds_remaining(deadline_at) <= urgent_remaining_seconds


def assert_time_left(deadline_at: datetime, need_seconds: float = 5.0) -> None:
    left = seconds_remaining(deadline_at)
    if left < need_seconds:
        raise TimeoutError(f"支付窗口不足: 剩余 {left:.1f}s < {need_seconds}s")
