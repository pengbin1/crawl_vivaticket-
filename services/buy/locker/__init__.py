from __future__ import annotations

__all__ = ["run_locker"]


def __getattr__(name: str):
    if name == "run_locker":
        from locker.pipeline import run_locker

        return run_locker
    raise AttributeError(name)
