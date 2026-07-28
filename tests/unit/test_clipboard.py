"""The clipboard must return exactly what it was given.

Reported from live use: copied text arrived with em dashes mangled. The cause was
`clip.exe`, which decodes piped bytes using the CONSOLE CODE PAGE rather than
UTF-8 - so the old pipe was correct only by the accident of the terminal it ran
in. Measured directly:

    chcp 65001  ->  A-B rocket (clean)
    chcp 1252   ->  mojibake
    chcp  437   ->  worse mojibake

The fix is encoding-correctness, not dash removal, because emoji and any future
non-ASCII have to survive too.

These tests SAVE AND RESTORE the clipboard around themselves. A test suite that
silently eats whatever you were holding would be committing the exact rudeness
this feature was written to avoid.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from voice_bridge import onboard

ROUND_TRIP = "em dash A\u2014B, quotes \u201cx\u201d, emoji \U0001f680, accents \u00e9\u00fc"


def _read_clipboard() -> str | None:
    """Current clipboard text, or None when there is no way to read it.

    The READER has to be encoding-explicit for the same reason the writer does,
    which this test found the hard way: piping `Get-Clipboard` through stdout sends
    it across the console code page and mangles exactly what we are checking for.
    So PowerShell writes UTF-8 to a file and we read the bytes, with no console in
    the path at all.
    """
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["pbpaste"], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None

    if sys.platform != "win32":
        return None

    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "clip.txt"
        script = (
            f"Get-Clipboard -Raw | Set-Content -LiteralPath '{target}' -Encoding UTF8 -NoNewline"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0 or not target.exists():
            return None
        # utf-8-sig: Windows PowerShell's Set-Content -Encoding UTF8 writes a BOM,
        # which is an artefact of this reader rather than anything the clipboard
        # did. Stripping it here keeps the assertion about the product.
        return target.read_text(encoding="utf-8-sig")


@pytest.fixture
def preserved_clipboard():
    """Restore whatever the user was holding, whatever this test does to it."""
    before = _read_clipboard()
    if before is None:
        pytest.skip("no readable clipboard on this platform/session")
    yield
    onboard.copy_to_clipboard(before)


def test_non_ascii_survives_the_real_copy_path(preserved_clipboard):
    """The round trip that the bug broke: what goes in must come out."""
    assert onboard.copy_to_clipboard(ROUND_TRIP) is True

    got = _read_clipboard()
    assert got is not None
    assert got.rstrip("\r\n") == ROUND_TRIP, "the clipboard mangled the text"


def test_the_em_dash_specifically_survives(preserved_clipboard):
    """Named because it is what a person actually noticed and reported."""
    assert onboard.copy_to_clipboard("before \u2014 after") is True
    got = _read_clipboard()
    assert got is not None
    assert "\u2014" in got
    assert "\u00e2\u20ac" not in got, "classic UTF-8-read-as-cp1252 mojibake"
