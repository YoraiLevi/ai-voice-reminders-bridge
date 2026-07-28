"""Every documented way to invoke the CLI must actually run it.

A module invocation that imports, defines main(), calls nothing and exits 0 is a
command that succeeds at nothing — the confident-wrong-answer class, in the one
place a user is most likely to try first.
"""

from __future__ import annotations

import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_python_m_voice_bridge_runs_the_cli():
    result = _run(["-m", "voice_bridge", "--help"])
    assert result.returncode == 0
    assert "A voice spoke for a file-mailbox" in result.stdout


def test_python_m_voice_bridge_cli_runs_the_cli():
    """The path the guard was missing from — it exited 0 having done nothing."""
    result = _run(["-m", "voice_bridge.cli", "--help"])
    assert result.returncode == 0
    assert "A voice spoke for a file-mailbox" in result.stdout


def test_a_module_invocation_reports_a_real_exit_code():
    """Exit codes must survive the module path, or scripts gating on them break."""
    result = _run(["-m", "voice_bridge", "config", "get", "no-such-field"])
    assert result.returncode == 2, "an unknown field is 'you must act', not success"
