"""log.configure: level selection, idempotency, file output."""

from __future__ import annotations

import logging

from voice_bridge import log


def test_levels():
    log.configure(verbose=True)
    assert log.get().level == logging.INFO
    log.configure(quiet=True)
    assert log.get().level == logging.ERROR
    log.configure()
    assert log.get().level == logging.WARNING


def test_no_duplicate_handlers():
    log.configure()
    n = len(log.get().handlers)
    log.configure()
    assert len(log.get().handlers) == n  # idempotent, no stacking


def test_logfile_writes(tmp_path):
    f = tmp_path / "vb.log"
    log.configure(verbose=True, logfile=str(f))
    log.get().info("hello world")
    for h in log.get().handlers:
        h.flush()
    assert "hello world" in f.read_text(encoding="utf-8")
    log.configure()  # reset (drop the file handler)
