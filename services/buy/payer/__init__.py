from __future__ import annotations

__all__ = ["pay_job"]


def __getattr__(name: str):
    if name == "pay_job":
        from payer.orchestrator import pay_job

        return pay_job
    raise AttributeError(name)
