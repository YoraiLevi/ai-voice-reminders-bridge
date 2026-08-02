"""`deliver` — consent before an external upload, and honesty after it.

This command uploads the CONTENTS of a file to GitHub. That makes it the only
command in the tool that hands your data to a third party, so the bar for both
the question it asks and the result it reports is higher than anywhere else:

* without a terminal and without `--yes` it called `input()` and died on EOF, so
  a scripted run crashed instead of refusing (DELIVER-2);
* the prompt asked to "publish ... to get a link", which understates it twice:
  it does not say the file's contents leave the machine, and a "private" gist is
  **unlisted, not secret** — anyone with the URL can read it (DELIVER-4);
* the banner result was discarded, so the URL printed, the phone was never
  notified, and the command still exited 0 (DELIVER-3).
"""

from __future__ import annotations

import subprocess

import pytest

from voice_bridge import deliver as deliver_mod


@pytest.fixture
def gist(monkeypatch, tmp_path):
    """A stubbed `gh` that succeeds, plus a file to publish."""
    target = tmp_path / "notes.md"
    target.write_text("some long content", encoding="utf-8")
    monkeypatch.setattr(deliver_mod.shutil, "which", lambda name: "/usr/bin/gh")

    calls: list[list[str]] = []

    def fake_run(cmd, capture_output=True, text=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://gist.github.com/abc123\n", stderr=""
        )

    monkeypatch.setattr(deliver_mod.subprocess, "run", fake_run)
    return {"file": str(target), "calls": calls}


# --------------------------------------------------------------------------- #
# DELIVER-2 — refuse without consent, never crash for the want of it
# --------------------------------------------------------------------------- #


def test_no_tty_without_yes_refuses_rather_than_crashing(sample_config, gist, monkeypatch, capsys):
    monkeypatch.setattr(deliver_mod, "_is_tty", lambda: False)
    rc = deliver_mod.deliver(sample_config, gist["file"])
    assert rc == 2
    assert gist["calls"] == [], "nothing may be uploaded without consent"
    assert "refusing" in capsys.readouterr().out.lower()


def test_yes_flag_is_consent_enough_without_a_tty(sample_config, gist, monkeypatch):
    monkeypatch.setattr(deliver_mod, "_is_tty", lambda: False)
    assert deliver_mod.deliver(sample_config, gist["file"], assume_yes=True) == 0
    assert gist["calls"], "explicit --yes must proceed"


def test_declining_the_prompt_uploads_nothing(sample_config, gist, monkeypatch):
    monkeypatch.setattr(deliver_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(deliver_mod, "_ask_consent", lambda prompt: False)
    assert deliver_mod.deliver(sample_config, gist["file"]) == 0
    assert gist["calls"] == []


# --------------------------------------------------------------------------- #
# DELIVER-4 — the question must describe what actually happens
# --------------------------------------------------------------------------- #


def test_consent_prompt_names_the_upload_and_the_real_visibility(sample_config, gist, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(deliver_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(
        deliver_mod, "_ask_consent", lambda prompt: seen.setdefault("p", prompt) and False
    )
    deliver_mod.deliver(sample_config, gist["file"])
    prompt = seen["p"].lower()
    assert "github" in prompt, "say where the data goes"
    assert "contents" in prompt or "upload" in prompt, "say that the file itself leaves"
    # "private" gists are unlisted, not secret: anyone with the link can read them.
    assert "anyone with the link" in prompt or "not secret" in prompt


def test_public_flag_changes_the_warning(sample_config, gist, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(deliver_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(
        deliver_mod, "_ask_consent", lambda prompt: seen.setdefault("p", prompt) and False
    )
    deliver_mod.deliver(sample_config, gist["file"], public=True)
    assert "public" in seen["p"].lower()
    assert "searchable" in seen["p"].lower() or "listed" in seen["p"].lower()


# --------------------------------------------------------------------------- #
# DELIVER-3 — the banner result must not be discarded
# --------------------------------------------------------------------------- #


def test_url_is_printed_before_any_banner_warning(sample_config, gist, monkeypatch, capsys):
    """The URL is the deliverable; it must survive a notification failure."""
    monkeypatch.setattr(deliver_mod.ntfy, "push", lambda *a, **k: _Failed())
    rc = deliver_mod.deliver(sample_config, gist["file"], assume_yes=True)
    out = capsys.readouterr().out
    assert rc == 0, "a failed banner must not lose the user their link"
    assert "https://gist.github.com/abc123" in out
    assert out.index("gist.github.com") < out.index("warn"), "URL first, caveat after"


def test_failed_banner_is_reported_not_swallowed(sample_config, gist, monkeypatch, capsys):
    monkeypatch.setattr(deliver_mod.ntfy, "push", lambda *a, **k: _Failed())
    deliver_mod.deliver(sample_config, gist["file"], assume_yes=True)
    out = capsys.readouterr().out.lower()
    assert "warn" in out and "phone" in out


def test_successful_banner_says_nothing_extra(sample_config, gist, monkeypatch, capsys):
    monkeypatch.setattr(deliver_mod.ntfy, "push", lambda *a, **k: _Sent())
    deliver_mod.deliver(sample_config, gist["file"], assume_yes=True)
    assert "warn" not in capsys.readouterr().out.lower()


class _Failed:
    status = "failed"
    detail = "connection refused"

    def __bool__(self):
        return False


class _Sent:
    status = "sent"
    detail = ""

    def __bool__(self):
        return True
