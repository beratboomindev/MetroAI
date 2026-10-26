"""Loglama yardımcıları."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False


def setup(verbose: bool = False, logfile: Path | None = None, console: bool = True) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("metro")
    if _CONFIGURED:
        return logger

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        logger.addHandler(stream)

    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    logger.propagate = False
    _CONFIGURED = True
    return logger


def get() -> logging.Logger:
    return logging.getLogger("metro")
