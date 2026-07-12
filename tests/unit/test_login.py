"""login.resolve_code: the flag / file / stdin selection (pure, no pyicloud)."""

from __future__ import annotations

import io

from voice_bridge.login import resolve_code


def test_code_from_flag():
    assert resolve_code(code="123456", code_file=None, code_stdin=False) == "123456"


def test_code_from_file(tmp_path):
    f = tmp_path / "code.txt"
    f.write_text("654321\n", encoding="utf-8")
    assert resolve_code(code=None, code_file=str(f), code_stdin=False) == "654321"


def test_code_from_stdin():
    assert (
        resolve_code(code=None, code_file=None, code_stdin=True, stdin=io.StringIO("111222\n"))
        == "111222"
    )
