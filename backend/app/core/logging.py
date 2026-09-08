"""Logging-Konfiguration. Schreibt nach stdout (systemd/journald) und optional
in eine Logdatei unter ``log_dir``."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        fileh = RotatingFileHandler(
            settings.log_dir / "netprobe.log", maxBytes=10 * 1024 * 1024, backupCount=3
        )
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        # Logverzeichnis nicht beschreibbar (z.B. Tests) -> nur stdout
        pass

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
