"""Ctrl-C at a prompt is a decision, not a crash.

The live demo produced a raw `KeyboardInterrupt` traceback at the typed-UNINSTALL
gate - the exact moment the user was being most careful. The order was global:
every interactive path, not that one prompt.

These tests are about the SHAPE of the fix as much as the behaviour. There is one
wrapper, so the property is checked once and holds everywhere; and the two things
that look alike at a prompt - a closed stream and a pressed key - are kept apart,
because they mean opposite things about whether a person is there.
"""

from __future__ import annotations

import builtins

import pytest

from voice_bridge import cli, prompting
from voice_bridge.prompting import Cancelled


def test_ctrl_c_at_a_prompt_raises_cancelled_not_keyboardinterrupt(monkeypatch):
    def interrupted(_prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupted)
    with pytest.raises(Cancelled):
        prompting.ask("anything? ")


def test_ctrl_c_at_a_secret_prompt_raises_cancelled(monkeypatch):
    def interrupted(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(prompting._getpass, "getpass", interrupted)
    with pytest.raises(Cancelled):
        prompting.ask_secret("password: ")


def test_eof_is_not_cancelled(monkeypatch):
    """The two are deliberately different.

    EOF means "nobody is there", which each caller answers with its own safe
    default. Ctrl-C means "a person is here and wants out", which stops the whole
    command. Collapsing them would make a piped run exit as though someone had
    pressed a key, and a pressed key take a default nobody chose.
    """

    def ended(_prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", ended)
    with pytest.raises(EOFError):
        prompting.ask("anything? ")


def test_the_cli_turns_cancelled_into_one_quiet_line(monkeypatch, capsys):
    """No traceback, and an exit code that says nothing happened."""
    monkeypatch.setattr(cli, "_dispatch", lambda _args: (_ for _ in ()).throw(Cancelled()))

    code = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert code == 1
    assert "cancelled" in out.lower()
    assert "Traceback" not in out
    assert "nothing was done" in out.lower()


def test_every_prompt_in_the_package_goes_through_the_wrapper():
    """The `_alive` lesson, enforced: a second copy is a second chance to be wrong.

    The teardown traceback existed because prompts were written one at a time. This
    fails if a new bare `input(` or `getpass.getpass(` appears anywhere in the
    package, which is the only way the sweep stays swept.
    """
    import ast
    from pathlib import Path

    # Parsed, not grepped: a docstring that *mentions* `input()` - and several here
    # do, explaining why the seam exists - is not a call, and a check that cannot
    # tell the difference gets disabled the first time it cries wolf.
    pkg = Path(prompting.__file__).parent
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        if path.name == "prompting.py":
            continue  # the one place allowed to call them
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            bare = isinstance(fn, ast.Name) and fn.id == "input"
            secret = (
                isinstance(fn, ast.Attribute)
                and fn.attr == "getpass"
                and isinstance(fn.value, ast.Name)
                and fn.value.id.endswith("getpass")
            )
            if bare or secret:
                offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, "prompts must go through voice_bridge.prompting: " + ", ".join(offenders)
