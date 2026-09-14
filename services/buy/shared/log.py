from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# services/buy/shared/log.py → monorepo root is parents[3]
_BUY_ROOT = Path(__file__).resolve().parents[1]
_MONOREPO_ROOT = Path(__file__).resolve().parents[3]


def cenacolo_home() -> Path:
    raw = os.getenv("CENACOLO_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _MONOREPO_ROOT


def buy_root() -> Path:
    return _BUY_ROOT


def default_log_dir() -> Path:
    return cenacolo_home() / "var" / "log"


def default_artifact_dir() -> Path:
    return cenacolo_home() / "var" / "artifacts"


_CONFIGURED = False


def setup_logging(
    service: str = "buy",
    *,
    level: int = logging.INFO,
    log_dir: Path | str | None = None,
    also_file: bool = True,
) -> None:
    """Configure root logging once: stdout (journald) + rotating file."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(level)
    root.addHandler(sh)

    if also_file:
        d = Path(log_dir) if log_dir else default_log_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                d / f"{service}.log",
                maxBytes=50 * 1024 * 1024,
                backupCount=14,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            fh.setLevel(level)
            root.addHandler(fh)
        except OSError as exc:
            # Still usable via stdout if file path not writable
            root.warning("file logging disabled: %s", exc)

    # Quiet noisy libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("WDM").setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger(f"cenacolo.{service}").info(
        "logging ready service=%s home=%s", service, cenacolo_home()
    )


def get_logger(name: str = "cenacolo_buy") -> logging.Logger:
    """Return a named logger. Entry points must call setup_logging() first."""
    if not name.startswith("cenacolo"):
        name = f"cenacolo.{name}"
    return logging.getLogger(name)
