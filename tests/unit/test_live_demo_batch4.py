"""The HOLD batch: act 1 re-run, and the demo stops until this lands.

Ruled: *"we cannot progress until things are implemented configurably correctly
and make no user/agent errors."* Two words in that sentence do the work. **User**
errors are ours to prevent by refusing. **Agent** errors are worse, because the
agent is a system we cannot test - it obeys a prompt we hand it, and if that
prompt names a list nobody selected, every layer here reports success while the
dictations pile up somewhere no one reads.

One root cause under most of it: the two display-name fields DEFAULTED to
"Vox-Message-Outbox" / "Vox-Message-Inbox". A fresh config therefore arrived
already claiming two lists, and every surface that renders the cache printed them
as fact. The fix is not a better message - it is that a cache with an invented
default is not a cache.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import onboard, prompt, runner, setup as setup_mod
from voice_bridge.config import DEFAULTS, load_config
from voice_bridge.errors import CommandError
from voice_bridge.selection import missing_roles, missing_roles_message


# --------------------------------------------------------------------------- #
# 1 - the root cause: a cache that invented its own contents
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", ["inbox_list", "output_list"])
def test_a_fresh_config_claims_no_list_names(field):
    """These are a record of what you chose. Before you choose, there is nothing
    to record, and a default here is an invention that later reads as evidence."""
    assert DEFAULTS[field] == ""


def test_a_fresh_config_has_nothing_selected(unselected_config):
    assert missing_roles(unselected_config) == ["inbox", "outbox"]


def test_the_names_to_CREATE_live_apart_from_the_names_you_CHOSE(unselected_config):
    """Radicale really does have to name the lists it makes, and the iCloud guide
    really does have to suggest something. Keeping those in `SUGGESTED_NAMES`
    means a suggestion can never be mistaken for a selection."""
    assert setup_mod.SUGGESTED_NAMES["inbox"]
    assert setup_mod.SUGGESTED_NAMES["outbox"]
    assert unselected_config.inbox_list == ""


# --------------------------------------------------------------------------- #
# 2 - P1: where the "[ok] machine round trip" came from
# --------------------------------------------------------------------------- #


def test_verify_refuses_when_a_role_is_unselected(unselected_config, fake_transport):
    """A TEST THAT CANNOT RUN MUST REFUSE, NOT PASS.

    What actually happened, traced: with `output_list_id` empty, `verify` called
    `resolve_list(cfg.output_list, "")` - and `cfg.output_list` was the FABRICATED
    default "Vox-Message-Inbox". On a real account that name matched a leftover
    list from an earlier setup, so the probe was written to it, read back from it,
    and reported ok. Every layer was honest about what it did; none was asked
    whether what it did meant anything.
    """
    with pytest.raises(CommandError) as err:
        setup_mod.verify(unselected_config, fake_transport)
    assert err.value.code == 2
    assert "lists --select" in err.value.msg


def test_icloud_never_falls_back_from_a_dead_id_to_a_name(monkeypatch):
    """The other half of the same bug, in the adapter.

    A given-but-unmatched id fell THROUGH to name matching, so a stale selection
    was answered with a guess. The fake and the CalDAV adapter already raised
    here; the iCloud one - the live one - did not.
    """
    from voice_bridge.icloud import ICloudTransport

    class _Lst:
        id, title = "REAL", "Vox-Message-Inbox"

    t = ICloudTransport.__new__(ICloudTransport)
    monkeypatch.setattr(t, "_svc", lambda: type("S", (), {"lists": lambda self: [_Lst()]})())

    with pytest.raises(LookupError) as err:
        t.resolve_list("Vox-Message-Inbox", "SELECTED-BUT-GONE")
    assert "lists --select" in str(err.value)


def test_icloud_refuses_a_lookup_with_no_id_and_no_name(monkeypatch):
    from voice_bridge.icloud import ICloudTransport

    t = ICloudTransport.__new__(ICloudTransport)
    monkeypatch.setattr(t, "_svc", lambda: type("S", (), {"lists": lambda self: []})())

    with pytest.raises(LookupError, match="no list id selected"):
        t.resolve_list("", "")


# --------------------------------------------------------------------------- #
# 3 - both roles are a hard requirement
# --------------------------------------------------------------------------- #


def test_setup_stops_before_the_round_trip_when_a_role_is_unselected(
    tmp_path, tmp_mailbox, monkeypatch, capsys, fake_transport
):
    """Ruled verbatim: "the setup shouldn't have continued to round trip until we
    had set the two lists, its a hard requirement for the run and system to work".

    The run that produced this ended "You are set up. Start the bridge with:
    voice-bridge run" - and `run` refused one command later. Setup made a claim the
    very next command disproved.
    """
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps({"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state")}),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "make_transport", lambda _cfg: fake_transport)
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(setup_mod, "settle_selection", lambda *a, **k: 0)

    rc = setup_mod.run_setup(config_path=cfg_file)
    out = capsys.readouterr().out

    assert rc == 2, "an incomplete setup must not exit 0"
    assert "cannot go further without both lists" in out
    assert "lists --select" in out
    assert "You are set up" not in out


def test_the_missing_role_message_names_the_role_and_the_verb():
    lines = missing_roles_message(["outbox"])
    text = " ".join(lines)
    assert "the list replies appear in" in text
    assert "lists --select" in text


# --------------------------------------------------------------------------- #
# 4 - the prompt refuses rather than shipping a phantom list to another system
# --------------------------------------------------------------------------- #


def test_vox_prompt_refuses_while_a_role_is_unselected(unselected_config):
    with pytest.raises(CommandError) as err:
        prompt.render_vox_prompt(unselected_config)
    assert err.value.code == 2


def test_a_rendered_prompt_never_names_a_list_that_was_not_chosen(sample_config):
    """The body and the AUTHORITATIVE section must agree, always. The live render
    that triggered this listed ONE id and named a made-up list in its text."""
    out = prompt.render_vox_prompt(sample_config)

    assert sample_config.inbox_list in out
    assert sample_config.output_list in out
    assert sample_config.inbox_list_id in out
    assert sample_config.output_list_id in out
    assert out.count("AUTHORITATIVE") == 1


# --------------------------------------------------------------------------- #
# 5 - the retry resumes AT the failure, and the failure is logged
# --------------------------------------------------------------------------- #


def test_the_transient_retry_does_not_replay_the_whole_flow(
    tmp_path, tmp_mailbox, monkeypatch, capsys
):
    """The batch-2 promise was an in-place retry OF THE FAILING OPERATION.

    It was implemented as a recursive `run_setup`, and the transcript showed what
    that really is: preamble re-asked, credentials re-walked, picker from scratch.
    A blip still cost the whole flow, just politely.
    """
    import requests

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list_id": "i1",
                "output_list_id": "o1",
            }
        ),
        encoding="utf-8",
    )

    welcomes = []
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    # `run_setup` imports onboard locally, so the module object is the seam.
    monkeypatch.setattr(onboard, "welcome", lambda show: welcomes.append(1))
    monkeypatch.setattr(onboard, "_yes", lambda *a, **k: True)
    # Step 1 already succeeded in the scenario under test - the blip happened
    # AFTER credentials and after a pick, which is the whole point.
    monkeypatch.setattr(onboard, "step_credentials", lambda *a, **k: True)
    monkeypatch.setattr(setup_mod, "prompt_fields", lambda cfg, preset: {})

    attempts = []

    def flaky(_cfg):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("Request failed")
        raise KeyboardInterrupt  # stop the run once the retry has been observed

    monkeypatch.setattr(setup_mod, "make_transport", flaky)

    with pytest.raises(BaseException):
        setup_mod.run_setup(config_path=cfg_file)

    assert len(attempts) == 2, "the failing operation must be retried"
    assert len(welcomes) <= 1, "the retry must not replay the preamble"


def test_a_transient_failure_is_logged_with_its_class_and_budget(sample_config, capsys, caplog):
    """Asked verbatim: "log what happened? it took a really long time?".

    A failure with no duration and no exception class is indistinguishable from
    the program hanging, so the user cannot tell whether to wait or to quit.
    """
    import logging

    import requests

    # Moved to `transient.report` in batch 5, so `lists --select` and `setup` say
    # the same thing. The property is unchanged; the seam is shared now.
    from voice_bridge import transient

    with caplog.at_level(logging.WARNING, logger="voice-bridge"):
        transient.report(requests.ConnectionError("Request failed"), sample_config)
    out = capsys.readouterr().out

    assert "ConnectionError" in out, "name the class; 'Request failed' identifies nothing"
    assert str(int(sample_config.icloud_timeout)) in out
    assert "--log-file" in out
    assert any("transient transport failure" in r.message for r in caplog.records)


def test_icloud_calls_get_a_client_side_deadline():
    """INVESTIGATED: the long wait was not our backoff - `_retrying` fires only on
    a throttle and spends ~7s at most. There was NO client-side timeout at all, so
    a hung connection was bounded only by the OS TCP stack."""
    from voice_bridge.icloud import _apply_timeout

    seen = {}

    class _Session:
        def request(self, *args, **kwargs):
            seen.update(kwargs)
            return "ok"

    api = type("Api", (), {"session": _Session()})()
    _apply_timeout(api, 12.5)
    api.session.request("GET", "https://example.test")

    assert seen["timeout"] == 12.5


def test_an_explicit_timeout_still_wins():
    """The default fills a gap; it does not override a caller with an opinion."""
    from voice_bridge.icloud import _apply_timeout

    seen = {}

    class _Session:
        def request(self, *args, **kwargs):
            seen.update(kwargs)

    api = type("Api", (), {"session": _Session()})()
    _apply_timeout(api, 30)
    api.session.request("GET", "u", timeout=1)

    assert seen["timeout"] == 1


# --------------------------------------------------------------------------- #
# 6 - validate before side effects
# --------------------------------------------------------------------------- #


def test_run_refuses_before_creating_the_mailbox_or_printing_instructions(
    tmp_path, tmp_mailbox, capsys
):
    """It created the mailbox, printed the two-file explainer AND the peer-join
    instructions, and only then said it could not run. Reading top-down, all of
    that was wasted - and two of them were actions taken on the user's disk."""
    cfg_file = tmp_path / "voice-bridge.json"
    mailbox = tmp_path / "not-yet"
    cfg_file.write_text(
        json.dumps({"mailbox_dir": str(mailbox), "state_dir": str(tmp_path / "state")}),
        encoding="utf-8",
    )

    rc = runner.run_command(config_path=cfg_file)
    out = capsys.readouterr().out

    assert rc == 2
    assert "No list is selected" in out
    assert "PEER" not in out.upper(), "no instructions for a run that will not start"
    assert not mailbox.exists(), "nothing on disk for a run that will not start"


# --------------------------------------------------------------------------- #
# 7 - say the invocation that actually works here
# --------------------------------------------------------------------------- #


def test_the_path_note_is_silent_when_the_command_is_typeable(monkeypatch):
    """A warning that fires when nothing is wrong is one people learn to scroll
    past, and then miss the time it matters."""
    from voice_bridge import invocation

    monkeypatch.setattr(invocation.shutil, "which", lambda _n: "/usr/bin/voice-bridge")
    assert invocation.path_note() == ""
    assert invocation.working_form() == "voice-bridge"


def test_the_path_note_names_the_uv_form_inside_a_project(monkeypatch, tmp_path):
    """The human followed our own advice and got "not recognized"."""
    from voice_bridge import invocation

    venv = tmp_path / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(invocation.shutil, "which", lambda _n: None)
    monkeypatch.setattr(invocation.sys, "argv", [str(venv / "voice-bridge.exe")])

    assert invocation.working_form() == "uv run voice-bridge"
    note = invocation.path_note()
    assert "not on your PATH" in note
    assert "uv run voice-bridge" in note


def test_the_module_form_is_reported_when_that_is_how_we_were_launched(monkeypatch):
    from voice_bridge import invocation

    monkeypatch.setattr(invocation.shutil, "which", lambda _n: None)
    monkeypatch.setattr(invocation.sys, "argv", ["/x/voice_bridge/cli.py"])

    assert "-m voice_bridge.cli" in invocation.working_form()


# --------------------------------------------------------------------------- #
# the positive: a defect class confirmed dead
# --------------------------------------------------------------------------- #


def test_stray_pasted_text_is_answered_line_by_line_and_survives():
    """Batch-3 item 13, verified in the wild rather than by us.

    Four garbage lines hit "Start the bridge now?" during the live run; each got
    "not an answer - type y or n", and the flow reached a clean n. The silent
    re-ask class is dead, and this is the regression guard.
    """
    answers = iter(["<paste>", "more junk", "!!", "", "n"])
    out: list[str] = []
    # The empty line is a bare Enter, which legitimately takes the shown default -
    # so it is placed AFTER the junk to prove the junk did not consume it.
    result = onboard._yes(lambda _p: next(answers), "Start the bridge now?", show=out.append)

    assert result is True, "a bare Enter still takes the displayed default"
    assert sum("not an answer" in line for line in out) == 3


def test_a_config_that_still_names_the_old_defaults_is_untouched(tmp_path, tmp_mailbox):
    """Someone mid-demo has "Vox-Message-Outbox" written in their file. Emptying
    the DEFAULT must not empty THEIR value."""
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list": "Vox-Message-Outbox",
            }
        ),
        encoding="utf-8",
    )
    assert load_config(cfg_file).inbox_list == "Vox-Message-Outbox"


def test_the_path_note_is_printed_to_stderr_before_anything_else(monkeypatch, capsys):
    """Coverage of this line must not depend on whose shell ran the tests.

    Found by an instrument delta: the manager's run missed exactly one statement
    that mine covered - `cli.main`'s `print(note, ...)`. The cause was real and
    benign: `uv run` puts the console script on PATH, so `path_note()` returns ""
    there and the branch never executes, while in a bare shell it fires. Two honest
    runs, one line apart, and the number moved.

    The finding is not the 0.04%: it is that a branch shipped in the same commit
    was covered BY ACCIDENT OF ENVIRONMENT. This pins it.

    stderr specifically, because `vox-prompt | clip` must pipe the prompt alone.
    """
    from voice_bridge import cli

    monkeypatch.setattr(cli, "path_note", lambda: "note: use `uv run voice-bridge`")
    cli.main(["config", "fields"])
    captured = capsys.readouterr()

    assert "uv run voice-bridge" in captured.err
    assert "uv run voice-bridge" not in captured.out, "advice must never pollute a pipe"


def test_no_path_note_is_printed_when_there_is_nothing_to_say(monkeypatch, capsys):
    from voice_bridge import cli

    monkeypatch.setattr(cli, "path_note", lambda: "")
    cli.main(["config", "fields"])

    assert capsys.readouterr().err == ""
