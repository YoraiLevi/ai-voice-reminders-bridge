"""Structured logging for the daemon. Operational events go through this (levels,
timestamps, optional file); user-facing command *results* stay on `print` so the CLI
is still pipeable."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_NAME = "voice-bridge"


def configure(
    *, verbose: bool = False, quiet: bool = False, logfile: str | Path | None = None
) -> None:
    """Set up the logger. Idempotent — repeated calls don't stack handlers.
    Level: WARNING default · INFO on -v · ERROR on -q."""
    level = logging.INFO if verbose else logging.ERROR if quiet else logging.WARNING
    logger = logging.getLogger(_NAME)
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)


def get(name: str = _NAME) -> logging.Logger:
    return logging.getLogger(name)
