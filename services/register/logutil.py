from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[2]


def cenacolo_home() -> Path:
    raw = os.getenv("CENACOLO_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _MONOREPO_ROOT


def setup_logging(service: str = "register", level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    log_dir = cenacolo_home() / "var" / "log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / f"{service}.log",
            maxBytes=50 * 1024 * 1024,
            backupCount=14,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        root.warning("file logging disabled: %s", exc)
