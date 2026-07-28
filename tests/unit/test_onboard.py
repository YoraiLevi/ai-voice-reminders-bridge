"""The guided setup conversation.

Every individual step already worked; the SEQUENCE was the gap. Someone finished
`setup` and still had to discover four more commands, none of which announced
itself — and knowing a command exists is not the same as being led to it.

Two rules govern the flow, both named by the person who has to use it: nothing
touches their machine or accounts without asking first, and every step says what
is happening and what comes next. Most of these tests guard the first, because it
is the one with teeth.
"""

from __future__ import annotations

import json

from voice_bridge import onboard


def _io(answers):
    """(ask, show, output-list) driven by scripted answers."""
    out: list[str] = []
    it = iter(answers)
    return (lambda _p: next(it)), out.append, out


# --------------------------------------------------------------------------- #
# the clipboard — the rule with teeth
# --------------------------------------------------------------------------- #


def test_the_clipboard_is_never_written_without_asking(sample_config, monkeypatch):
    """Ruled from live use: the user may be holding something they care about, so
    it cannot be overwritten just because writing it would be convenient."""
    copied: list[str] = []
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda text: copied.append(text) or True)

    ask, show, out = _io(["n"])
    onboard.step_phone_prompt(sample_config, ask=ask, show=show)

    assert copied == [], "declining must leave the clipboard untouched"
    joined = "\n".join(out)
    assert "the VOICE of my agent system" in joined, "declining must still hand over the prompt"
    assert "paste the text above" in joined


def test_the_clipboard_default_is_no(sample_config, monkeypatch):
    """A bare Enter must not overwrite it either: the safe answer is the default."""
    copied: list[str] = []
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda text: copied.append(text) or True)

    ask, show, out = _io([""])
    onboard.step_phone_prompt(sample_config, ask=ask, show=show)

    assert copied == []
    assert "the VOICE of my agent system" in "\n".join(out)


def test_accepting_copies_and_says_what_to_do_next(sample_config, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda text: copied.append(text) or True)

    ask, show, out = _io(["y"])
    onboard.step_phone_prompt(sample_config, ask=ask, show=show)

    assert copied and "the VOICE of my agent system" in copied[0]
    joined = "\n".join(out)
    assert "copied." in joined
    # Ruled: the prompt is ALWAYS printed. Showing it only when the copy is
    # declined would leave someone who said yes with nothing on screen to check.
    assert "the VOICE of my agent system" in joined


def test_a_failed_copy_says_so_and_the_prompt_is_still_there(sample_config, monkeypatch):
    """No clipboard tool is not a dead end — the prompt is the deliverable, and it
    was already on screen before the question was asked."""
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda text: False)

    ask, show, out = _io(["y"])
    onboard.step_phone_prompt(sample_config, ask=ask, show=show)
    joined = "\n".join(out)

    assert "the VOICE of my agent system" in joined
    assert "no clipboard tool" in joined, "it must not silently do nothing"


# --------------------------------------------------------------------------- #
# notifications — four real options, including someone else's server
# --------------------------------------------------------------------------- #


def test_the_suggested_topic_is_unguessable():
    """A topic is a public URL on a public server: anyone who knows the name can
    read every reply. Memorable is exactly what would make it guessable."""
    first, second = onboard.suggest_topic(), onboard.suggest_topic()
    assert first != second
    assert len(first) > 12


def test_option_one_saves_the_suggested_topic(sample_config, tmp_path):
    ask, show, out = _io(["1"])
    assert onboard.step_notifications(
        sample_config, config_path=tmp_path / "c.json", ask=ask, show=show
    )
    saved = sample_config.ntfy_topic_file.read_text(encoding="utf-8").strip()
    joined = "\n".join(out)

    assert saved.startswith("vox-")
    assert saved in joined, "it must show the topic it saved"
    assert str(sample_config.ntfy_topic_file) in joined, "and where it saved it"


def test_option_two_accepts_a_topic_you_already_have(sample_config, tmp_path):
    ask, show, _ = _io(["2", "my-existing-topic"])
    onboard.step_notifications(sample_config, config_path=tmp_path / "c.json", ask=ask, show=show)
    assert sample_config.ntfy_topic_file.read_text(encoding="utf-8").strip() == "my-existing-topic"


def test_option_three_supports_a_self_hosted_server(sample_config, tmp_path):
    """Someone running their own ntfy must not have to skip the step and go
    hand-edit a config file afterwards."""
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text("{}", encoding="utf-8")

    ask, show, _ = _io(["3", "https://ntfy.example.org", "private-topic"])
    onboard.step_notifications(sample_config, config_path=cfg_file, ask=ask, show=show)

    written = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert written["ntfy_server"] == "https://ntfy.example.org"
    assert sample_config.ntfy_topic_file.read_text(encoding="utf-8").strip() == "private-topic"


def test_skipping_says_what_is_lost_and_how_to_do_it_later(sample_config, tmp_path):
    ask, show, out = _io(["4"])
    assert not onboard.step_notifications(
        sample_config, config_path=tmp_path / "c.json", ask=ask, show=show
    )
    joined = "\n".join(out)

    assert "still arrive in your list" in joined, "what you lose must be stated"
    assert str(sample_config.ntfy_topic_file) in joined, "and how to set it later"
    assert not sample_config.ntfy_topic_file.exists()


def test_an_unusable_choice_re_asks(sample_config, tmp_path):
    ask, show, out = _io(["9", "4"])
    onboard.step_notifications(sample_config, config_path=tmp_path / "c.json", ask=ask, show=show)
    assert "please choose" in "\n".join(out)


# --------------------------------------------------------------------------- #
# credentials — offered inline, not named in a footnote
# --------------------------------------------------------------------------- #


def test_credentials_step_offers_the_login_inline(sample_config):
    ran: list[bool] = []
    ask, show, _ = _io(["y"])
    assert onboard.step_credentials(
        sample_config, ask=ask, show=show, login=lambda: ran.append(True) or 0
    )
    assert ran == [True]


def test_declining_credentials_names_the_command(sample_config):
    ask, show, out = _io(["n"])
    assert not onboard.step_credentials(sample_config, ask=ask, show=show, login=lambda: 0)
    assert "voice-bridge icloud-login" in "\n".join(out)


def test_existing_credentials_are_not_re_asked(sample_config):
    sample_config.creds_env.parent.mkdir(parents=True, exist_ok=True)
    sample_config.creds_env.write_text("ICLOUD_APPLE_ID=x\n", encoding="utf-8")

    ask, show, out = _io([])  # no answers available: asking would raise
    assert onboard.step_credentials(sample_config, ask=ask, show=show, login=lambda: 0)
    assert "already configured" in "\n".join(out)


# --------------------------------------------------------------------------- #
# the narration itself
# --------------------------------------------------------------------------- #


def test_every_step_says_what_comes_next(sample_config, monkeypatch, tmp_path):
    """Being told what is happening and what follows is what makes this read as
    guidance rather than as something acting on your behalf."""
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda text: True)
    out: list[str] = []

    onboard.step_credentials(sample_config, ask=lambda _p: "n", show=out.append, login=lambda: 0)
    onboard.step_notifications(
        sample_config, config_path=tmp_path / "c.json", ask=lambda _p: "4", show=out.append
    )
    onboard.step_phone_prompt(sample_config, ask=lambda _p: "y", show=out.append)

    joined = "\n".join(out)
    for marker in ("[1/5]", "[3/5]", "[5/5]", "next:"):
        assert marker in joined


def test_the_welcome_promises_nothing_happens_unasked(sample_config):
    out: list[str] = []
    onboard.welcome(out.append)
    joined = "\n".join(out).lower()

    assert "without asking" in joined
    assert "skip" in joined


# --------------------------------------------------------------------------- #
# the edges: a stream that ends, and a machine with no clipboard tool
# --------------------------------------------------------------------------- #


def test_a_closed_stream_is_handled_everywhere_a_question_is_asked():
    """isatty lies under MSYS/Git Bash, so EOF must never reach the user as a hang.

    This test used to assert that EOF took the DISPLAYED default - which encoded a
    real bug: on "Start the bridge now? [Y/n]" that meant a scripted run launched a
    poller nobody asked for. EOF is now always No; see
    test_a_closed_stream_never_authorises_an_action.
    """

    def _eof(_prompt):
        raise EOFError

    assert onboard._yes(_eof, "carry on?", default=True) is False
    assert onboard._yes(_eof, "overwrite?", default=False) is False


def test_a_closed_stream_skips_notifications_rather_than_inventing_a_topic(sample_config, tmp_path):
    """Generating a topic for someone who never answered would be the flow acting
    on their behalf — exactly what it exists to avoid."""

    def _eof(_prompt):
        raise EOFError

    assert not onboard.step_notifications(
        sample_config, config_path=tmp_path / "c.json", ask=_eof, show=lambda _s: None
    )
    assert not sample_config.ntfy_topic_file.exists()


def test_an_empty_topic_re_asks_instead_of_saving_nothing(sample_config, tmp_path):
    ask, show, _ = _io(["2", "", "2", "real-topic"])
    onboard.step_notifications(sample_config, config_path=tmp_path / "c.json", ask=ask, show=show)
    assert sample_config.ntfy_topic_file.read_text(encoding="utf-8").strip() == "real-topic"


def test_clipboard_reports_failure_when_no_tool_exists(monkeypatch):
    """A machine with no clipboard utility must say so rather than pretend."""
    monkeypatch.setattr(onboard.sys, "platform", "linux")
    monkeypatch.setattr(onboard.shutil, "which", lambda _name: None)
    assert onboard.copy_to_clipboard("text") is False


def test_clipboard_reports_failure_when_the_tool_errors(monkeypatch):
    monkeypatch.setattr(onboard.sys, "platform", "linux")
    monkeypatch.setattr(onboard.shutil, "which", lambda name: "/usr/bin/xclip")

    def _boom(*a, **k):
        raise OSError("no display")

    monkeypatch.setattr(onboard.subprocess, "run", _boom)
    assert onboard.copy_to_clipboard("text") is False


def test_the_farewell_distinguishes_finished_from_incomplete():
    done: list[str] = []
    onboard.farewell(done.append, ready=True)
    assert "voice-bridge run" in "\n".join(done)

    partial: list[str] = []
    onboard.farewell(partial.append, ready=False)
    joined = "\n".join(partial)
    assert "incomplete" in joined
    assert "doctor" in joined, "it must name the command that reports what is missing"


def test_the_prompt_is_printed_before_the_clipboard_question(sample_config, monkeypatch):
    """Ruled at 07:40: "The prompt should always be printed. But copying to
    clipboard should be conditional."

    Order matters as much as the fact: the prompt appears BEFORE the question, so
    the user is never answering about something they have not seen.
    """
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda text: True)
    asked_at: list[int] = []
    out: list[str] = []

    def ask(_prompt):
        asked_at.append(len(out))
        return "n"

    onboard.step_phone_prompt(sample_config, ask=ask, show=out.append)

    prompt_line = next(i for i, line in enumerate(out) if "the VOICE of my agent system" in line)
    assert prompt_line < asked_at[0], "the prompt must be on screen before the question"


def test_a_closed_stream_never_authorises_an_action(sample_config):
    """Found by running the guided flow, not by a test.

    A scripted setup hit EOF on "Start the bridge now? [Y/n]", took the DISPLAYED
    default of Yes, and launched a poller that never returned. On iCloud that would
    have started polling a live account unattended.

    A bare Enter is a real keystroke and honours the shown default; a closed stream
    is not an answer and must never say yes.
    """

    def _eof(_prompt):
        raise EOFError

    assert onboard._yes(_eof, "start something?", default=True) is False
    assert onboard._yes(_eof, "overwrite something?", default=False) is False


def test_a_bare_enter_still_honours_the_shown_default(sample_config):
    """The other half: a real person pressing Enter meant the default."""
    assert onboard._yes(lambda _p: "", "carry on?", default=True) is True
    assert onboard._yes(lambda _p: "", "overwrite?", default=False) is False
