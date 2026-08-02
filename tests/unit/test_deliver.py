"""deliver: gh-missing / decline / success (mocked gh + ntfy)."""

from __future__ import annotations

from voice_bridge import deliver


def test_gh_missing_returns_2(sample_config, monkeypatch):
    monkeypatch.setattr(deliver.shutil, "which", lambda _: None)
    assert deliver.deliver(sample_config, "f.md", assume_yes=True) == 2


def test_decline_publishes_nothing(sample_config, monkeypatch):
    monkeypatch.setattr(deliver.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(deliver, "_is_tty", lambda: True)
    monkeypatch.setattr(deliver, "_ask_consent", lambda prompt: False)
    called = {"n": 0}
    monkeypatch.setattr(deliver.subprocess, "run", lambda *a, **k: called.update(n=1))
    assert deliver.deliver(sample_config, "f.md") == 0
    assert called["n"] == 0  # never ran gh


def test_success_pushes_click_url(sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr(deliver.shutil, "which", lambda _: "/usr/bin/gh")

    class _R:
        returncode = 0
        stdout = "https://gist.github.com/abc123\n"
        stderr = ""

    monkeypatch.setattr(deliver.subprocess, "run", lambda *a, **k: _R())
    pushed: dict = {}
    monkeypatch.setattr(
        deliver.ntfy,
        "push",
        lambda cfg, text, click=None: pushed.update(url=click, text=text) or _Sent(),
    )
    f = tmp_path / "doc.md"
    f.write_text("content", encoding="utf-8")
    assert deliver.deliver(sample_config, str(f), summary="ready", assume_yes=True) == 0
    assert pushed["url"] == "https://gist.github.com/abc123"
    assert pushed["text"] == "ready"


class _Sent:
    """`ntfy.push` returns a 3-state result now, not a bool."""

    status = "sent"
    detail = ""

    def __bool__(self) -> bool:
        return True
