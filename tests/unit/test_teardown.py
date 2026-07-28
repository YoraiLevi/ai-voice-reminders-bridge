"""`reset` and `uninstall` - the two commands that can lose something.

These are the only commands here whose mistakes are unrecoverable, so the tests
are mostly about what they must NOT do: not delete an unlisted path, not proceed
without a typed word, not run while something is using the files, and not touch an
account. The happy paths matter less than the refusals.

`uninstall` removes everything by default and opts out with `--keep-*`, ruled by
the user after hearing the argument for the opposite default.
"""

from __future__ import annotations

import dataclasses

import pytest

from voice_bridge import teardown


def _io(answers):
    out: list[str] = []
    it = iter(answers)
    return (lambda _p: next(it)), out.append, out


@pytest.fixture
def installed(sample_config, tmp_path):
    """A config with every artifact actually on disk, so removal is observable."""
    cfg = sample_config
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text("{}", encoding="utf-8")

    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.creds_env.write_text("ICLOUD_APPLE_ID=x\n", encoding="utf-8")
    cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
    (cfg.cookie_dir / "cookie").write_text("c", encoding="utf-8")
    cfg.ntfy_topic_file.write_text("topic\n", encoding="utf-8")
    (cfg.state_dir / "seen").mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "seen" / "vox-inbox-seen.txt").write_text("id\n", encoding="utf-8")

    cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
    cfg.peer_inbox.write_text("- [10:00] (vox) a message\n", encoding="utf-8")
    cfg.our_inbox.write_text("- [10:01] (manager) a reply\n", encoding="utf-8")
    return cfg, cfg_file


def _run(cfg, cfg_file, verb, answers=("",), **kw):
    ask, show, out = _io(answers)
    code = teardown.run_teardown(
        cfg, cfg_file, verb=verb, ask=ask, show=show, is_tty=lambda: True, **kw
    )
    return code, "\n".join(out)


# --------------------------------------------------------------------------- #
# what must survive
# --------------------------------------------------------------------------- #


def test_reset_removes_credentials_by_default(installed):
    """The purpose-2 regression guard.

    reset exists so setup and login can be run again from zero. An earlier design
    put credentials behind an opt-in flag, which meant the DEFAULT reset left you
    logged in and could not demo a login at all - the thing the command is for was
    behind a flag.
    """
    cfg, cfg_file = installed
    code, _ = _run(cfg, cfg_file, "reset", ["RESET"])

    assert code == 0
    assert not cfg_file.exists()
    assert not cfg.creds_env.exists(), "credentials ARE the point of reset"
    assert not cfg.cookie_dir.exists(), "and so is the trusted session"


def test_reset_never_removes_the_mailbox(installed):
    """The load-bearing one for reset. Settings are ours; messages are not."""
    cfg, cfg_file = installed
    code, _ = _run(cfg, cfg_file, "reset", ["RESET"])

    assert code == 0
    assert cfg.mailbox_dir.exists()
    assert cfg.peer_inbox.exists() and cfg.our_inbox.exists()


def test_reset_spares_self_hosted_lists_but_not_server_settings(installed):
    """The asymmetry the redesign exposed: on iCloud your lists live on your Apple
    account, which no local command touches. On Radicale they live in the state
    directory. "We do not delete your lists" must not depend on where you host them.
    """
    cfg, cfg_file = installed
    collections = cfg.state_dir / "radicale" / "collections"
    collections.mkdir(parents=True, exist_ok=True)
    (collections / "list.ics").write_text("BEGIN:VCALENDAR", encoding="utf-8")
    (cfg.state_dir / "radicale" / "config").write_text("[server]", encoding="utf-8")

    code, _ = _run(cfg, cfg_file, "reset", ["RESET"])

    assert code == 0
    assert (collections / "list.ics").exists(), "your lists survive a reset"
    assert not (cfg.state_dir / "radicale" / "config").exists(), "generated settings do not"


def test_uninstall_removes_everything_by_default(installed):
    """The ruled default: complete unless told otherwise."""
    cfg, cfg_file = installed
    code, _ = _run(cfg, cfg_file, "uninstall", ["UNINSTALL"])

    assert code == 0
    assert not cfg_file.exists()
    assert not cfg.state_dir.exists()
    assert not cfg.mailbox_dir.exists(), "the mailbox goes too, by default"


def test_keep_mailbox_spares_the_mailbox_and_only_that(installed):
    """The single opt-out. Keeping your conversation history while removing the
    software is coherent; keeping your config or credentials is not an uninstall,
    and both of those land exactly where reset lands."""
    cfg, cfg_file = installed
    code, _ = _run(cfg, cfg_file, "uninstall", ["UNINSTALL"], keep_mailbox=True)

    assert code == 0
    assert cfg.mailbox_dir.exists() and cfg.peer_inbox.exists()
    assert not cfg_file.exists(), "everything else still goes"
    assert not cfg.state_dir.exists()


# --------------------------------------------------------------------------- #
# consent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("answer", ["", "y", "yes", "uninstall", "UNINSTAL", "delete"])
def test_anything_but_the_typed_word_deletes_nothing(installed, answer):
    """A y/N reflex is what makes destructive prompts dangerous, so the whole word
    is required - and lowercase does not count, because case-sensitivity is the
    friction, not an accident to forgive."""
    cfg, cfg_file = installed
    code, out = _run(cfg, cfg_file, "uninstall", [answer])

    assert code == 2
    assert "nothing was removed" in out
    assert cfg.mailbox_dir.exists() and cfg_file.exists()


def test_a_closed_stream_is_not_consent(installed):
    cfg, cfg_file = installed

    def _eof(_p):
        raise EOFError

    out: list[str] = []
    code = teardown.run_teardown(
        cfg, cfg_file, verb="uninstall", ask=_eof, show=out.append, is_tty=lambda: True
    )
    assert code == 2
    assert cfg.mailbox_dir.exists()


def test_non_tty_without_yes_refuses_and_says_why(installed):
    cfg, cfg_file = installed
    out: list[str] = []
    code = teardown.run_teardown(
        cfg,
        cfg_file,
        verb="uninstall",
        ask=lambda _p: "UNINSTALL",
        show=out.append,
        is_tty=lambda: False,
    )
    joined = "\n".join(out)

    assert code == 2
    assert "not a terminal" in joined and "--yes" in joined
    assert cfg.mailbox_dir.exists()


def test_yes_skips_the_prompt(installed):
    cfg, cfg_file = installed
    out: list[str] = []
    code = teardown.run_teardown(
        cfg,
        cfg_file,
        verb="uninstall",
        assume_yes=True,
        ask=lambda _p: pytest.fail("must not ask when --yes was given"),
        show=out.append,
        is_tty=lambda: False,
    )
    assert code == 0
    assert not cfg.mailbox_dir.exists()


# --------------------------------------------------------------------------- #
# the preview is the thing being consented to
# --------------------------------------------------------------------------- #


def test_the_preview_names_every_path_it_will_delete(installed):
    cfg, cfg_file = installed
    _, out = _run(cfg, cfg_file, "uninstall", [""])

    for path in (cfg_file, cfg.creds_env, cfg.ntfy_topic_file, cfg.peer_inbox, cfg.our_inbox):
        assert str(path) in out, f"{path} would be deleted but was not previewed"


def test_the_preview_names_the_mailbox_messages_not_just_the_directory(installed):
    """Consent is to losing MESSAGES; a dirname hides what that means."""
    cfg, cfg_file = installed
    _, out = _run(cfg, cfg_file, "uninstall", [""])

    assert "to-manager.md" in out
    assert "to-vox.md" in out


def test_missing_paths_are_shown_as_not_present(sample_config, tmp_path):
    """The preview is an account of what was CONSIDERED, not only what exists."""
    cfg_file = tmp_path / "voice-bridge.json"
    ask, show, out = _io([""])
    teardown.run_teardown(
        sample_config, cfg_file, verb="uninstall", ask=ask, show=show, is_tty=lambda: True
    )
    assert "(not present)" in "\n".join(out)


def test_kept_paths_are_named_with_their_reason(installed):
    """A survivor of a command called `uninstall` is a surprise unless it said so."""
    cfg, cfg_file = installed
    _, out = _run(cfg, cfg_file, "uninstall", [""], keep_mailbox=True)

    assert str(cfg.mailbox_dir) in out
    assert "--keep-mailbox" in out


def test_the_log_file_is_named_as_never_ours(installed):
    cfg, cfg_file = installed
    _, out = _run(cfg, cfg_file, "uninstall", [""])
    assert "--log-file" in out and "you chose that path" in out


def test_the_preview_says_no_account_is_touched(installed):
    """Reminder lists and gists live elsewhere; a machine teardown must not reach
    into an account, and must say so where the user is deciding."""
    cfg, cfg_file = installed
    _, out = _run(cfg, cfg_file, "uninstall", [""])
    assert "account" in out.lower()
    assert "gist" in out.lower()


def test_nothing_to_remove_exits_zero(sample_config, tmp_path):
    ask, show, out = _io([])
    code = teardown.run_teardown(
        sample_config,
        tmp_path / "absent.json",
        verb="reset",
        ask=ask,
        show=show,
        is_tty=lambda: True,
    )
    assert code == 0
    assert "Nothing to remove" in "\n".join(out)


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #


def test_refuses_while_a_bridge_is_running(installed, monkeypatch):
    """Deleting state under a live poller strands its open files and re-delivers
    from a cleared dedupe list."""
    from voice_bridge import poller

    cfg, cfg_file = installed
    monkeypatch.setattr(poller, "_another_poller_is_running", lambda c: 4321)

    code, out = _run(cfg, cfg_file, "uninstall", ["UNINSTALL"])

    assert code == 2
    assert "4321" in out and "Stop it first" in out
    assert cfg.mailbox_dir.exists(), "it must delete nothing at all"
    assert cfg_file.exists()


def test_refuses_while_the_radicale_server_is_running(installed, monkeypatch):
    from voice_bridge import poller, server

    cfg, cfg_file = installed
    cfg = dataclasses.replace(cfg, transport="radicale")
    monkeypatch.setattr(poller, "_another_poller_is_running", lambda c: None)
    monkeypatch.setattr(server, "status", lambda c: {"pid": 999})

    code, out = _run(cfg, cfg_file, "uninstall", ["UNINSTALL"])

    assert code == 2
    assert "radicale-server stop" in out
    assert cfg.mailbox_dir.exists()


def test_a_path_that_cannot_be_removed_is_reported_not_counted_as_done(installed, monkeypatch):
    """A teardown that prints "complete" while a locked file survived is the
    false-success class this project exists to remove."""
    cfg, cfg_file = installed

    real_unlink = teardown.Path.unlink

    def _refuse(self, *a, **k):
        if self.name == "icloud.env":
            raise OSError("in use by another process")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(teardown.Path, "unlink", _refuse)
    code, out = _run(cfg, cfg_file, "uninstall", ["UNINSTALL"])

    assert code == 2, "a failure must not exit 0"
    assert "FAILED" in out and "in use by another process" in out
    assert "could not remove" in out


def test_surrounding_whitespace_is_still_consent(installed):
    """Deliberately tolerated: someone who typed the entire word, then a stray
    space, has not answered reflexively. The friction is the word, not the
    keystrokes around it - punishing a trailing space would be theatre rather than
    safety.
    """
    cfg, cfg_file = installed
    code, _ = _run(cfg, cfg_file, "uninstall", ["  UNINSTALL  "])
    assert code == 0
    assert not cfg.mailbox_dir.exists()


def test_neither_command_touches_the_transport(installed, monkeypatch):
    """Reminder lists and published gists live on accounts, not this machine. A
    teardown verb that quietly reached into iCloud would be a destructive action
    nobody asked for, under a name that sounds like local tidying. The strongest
    proof is that no transport is ever constructed."""
    from voice_bridge import factory

    def _forbidden(_cfg):
        raise AssertionError("teardown must never build a transport")

    monkeypatch.setattr(factory, "make_transport", _forbidden)
    cfg, cfg_file = installed

    assert _run(cfg, cfg_file, "uninstall", ["UNINSTALL"])[0] == 0
