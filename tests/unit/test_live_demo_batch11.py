"""The transport switch: a first-class journey nobody had built.

The human tried to move an existing install from iCloud to Radicale by re-running
`setup` and answering the transport question. Everything after that answer went
wrong in the same way, and the way is the finding: **the transport was stated in
two places.**

`run_setup(transport=...)` is a REQUEST (from `--transport`, default `icloud`). The
config file is the DECISION, and the preamble may have just overridden the request.
Two lines branched on the request:

* the credentials step, so a radicale user was asked for an "Apple ID (email)" and
  pyicloud ran SRP against Apple with it - a 400 on empty input;
* the radicale server check, which was skipped entirely.

Meanwhile `make_transport(cfg)` read the FILE and built the CalDAV adapter. Every
step keyed off `cfg.transport` was correct; every step keyed off the parameter was
wrong. That is not a bug with two symptoms, it is one fact recorded twice.

And the write was not atomic: `transport=radicale` landed on disk while two CloudKit
list ids stayed behind, leaving the hybrid config that the Radicale runbook's
section 0 describes as a hazard - except the product performed it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from voice_bridge import doctor as doctor_mod, factory, onboard, runner, setup as setup_mod
from voice_bridge.config import load_config
from voice_bridge.transport import NotSupportedError


def _write_cfg(tmp_path, tmp_mailbox, **extra) -> Path:
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return cfg_file


def _scripted(answers: dict[str, str], asked: list[str]):
    """An `_ask_line` that answers by substring and records every prompt shown."""

    def ask(prompt: str) -> str:
        asked.append(prompt)
        for needle, reply in answers.items():
            if needle in prompt:
                return reply
        return ""

    return ask


# --------------------------------------------------------------------------- #
# 1a/1b - one source of truth for the transport
# --------------------------------------------------------------------------- #


def test_no_step_in_run_setup_branches_on_the_transport_PARAMETER():
    """Enumerated, not remembered - the batch-9 move.

    Two lines out of nine transport-conditional sites read the stale parameter. A
    reviewer cannot see that by reading either line; it is only visible by asking the
    whole function at once. So this asks.
    """
    tree = ast.parse(Path(setup_mod.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_setup")

    offenders = [
        f"setup.py:{node.lineno}"
        for node in ast.walk(fn)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "transport"
    ]
    assert not offenders, "branching on the requested transport, not the decided one: " + ", ".join(
        offenders
    )

    # And the parameter is DELETED once the file has the answer, so a future edit
    # that reaches for it fails loudly instead of quietly reading a stale value.
    assert any(
        isinstance(n, ast.Delete) and any(getattr(t, "id", "") == "transport" for t in n.targets)
        for n in ast.walk(fn)
    ), "the request must be retired once the decision is on disk"


def test_the_preamble_answer_decides_which_credentials_step_runs(
    tmp_path, tmp_mailbox, monkeypatch, capsys
):
    """The live failure, end to end: answer radicale, get asked for an Apple ID.

    `transport=icloud` is the parameter's default and stays untouched, exactly as it
    was on the run that broke - the ONLY thing that says radicale is the answer.
    """
    cfg_file = _write_cfg(tmp_path, tmp_mailbox)
    asked: list[str] = []
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(setup_mod, "_ask_line", _scripted({"transport": "radicale"}, asked))

    def _no(*_a, **_k):
        raise AssertionError("the iCloud credentials step must not run on radicale")

    monkeypatch.setattr(onboard, "step_credentials", _no)

    rc = setup_mod.run_setup(config_path=cfg_file, transport="icloud")
    out = capsys.readouterr().out

    # 2, not 0: this config has no radicale credentials, and since batch 19e that is
    # an OBSTACLE rather than a decline. The old 0 here was inherited from the defect,
    # not asserted about it - this test's subject is WHICH step runs.
    assert rc == 2
    assert load_config(cfg_file).transport == "radicale"
    assert not any("Apple ID" in p for p in asked), "no Apple prompt on a CalDAV transport"
    assert "radicale-server init" in out, "name where the credentials come from"


def test_the_radicale_credentials_step_asks_for_nothing(tmp_path, tmp_mailbox, capsys):
    """Deliberately not a prompt.

    Radicale's credentials are CREATED by `radicale-server init`, which also writes
    the server config and the bcrypt user file. Prompting for a username and
    password we cannot register anywhere would hand back credentials for an account
    that does not exist - a worse failure than saying where they come from.
    """
    cfg_file = _write_cfg(tmp_path, tmp_mailbox, transport="radicale")
    out: list[str] = []

    outcome = onboard.step_radicale_credentials(load_config(cfg_file), show=out.append)
    text = "\n".join(out)

    # BLOCKED, never DECLINED - this step asks nothing, so there is nothing to turn
    # down, and the difference is the exit code the caller reports (batch 19e).
    assert outcome == onboard.BLOCKED
    assert "radicale-server init" in text
    assert "Apple" not in text


def test_the_radicale_step_accepts_credentials_that_init_already_wrote(tmp_path, tmp_mailbox):
    cfg_file = _write_cfg(tmp_path, tmp_mailbox, transport="radicale")
    cfg = load_config(cfg_file)
    from voice_bridge.server import radicale_creds_path

    creds = radicale_creds_path(cfg)
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("ICLOUD_CALDAV_URL=http://x\n", encoding="utf-8")

    assert onboard.step_radicale_credentials(cfg, show=lambda _s: None) == onboard.OK


# --------------------------------------------------------------------------- #
# 1b - the switch is atomic-ish: no hybrid config survives it
# --------------------------------------------------------------------------- #


def test_switching_transport_clears_the_foreign_list_ids(tmp_path, tmp_mailbox):
    """The hybrid on their disk right now: radicale + two CloudKit ids.

    An id is issued BY a backend. Carrying it across is not conservative, it is a
    config that claims a list the new backend has never heard of.
    """
    cfg_file = _write_cfg(
        tmp_path,
        tmp_mailbox,
        transport="icloud",
        inbox_list_id="B1CC-cloudkit",
        output_list_id="825C-cloudkit",
        inbox_list="Test new List",
        output_list="Test2 Cloud Ai",
    )

    notes = setup_mod.write_config(cfg_file, transport="radicale")
    cfg = load_config(cfg_file)

    assert cfg.transport == "radicale"
    assert cfg.inbox_list_id == "" and cfg.output_list_id == ""
    assert cfg.inbox_list == "" and cfg.output_list == "", "the display cache goes too"
    text = "\n".join(notes)
    assert "icloud -> radicale" in text
    assert "choose both lists again" in text, "say the lists step is coming"


def test_a_config_never_holds_ids_from_another_transport(tmp_path, tmp_mailbox):
    """The invariant stated as one assertion, in both directions.

    "Write the transport last" was the other candidate fix and it only moves the
    window: the ids would still be foreign for as long as the flow ran. One write
    that leaves a consistent state has no window at all.
    """
    cfg_file = _write_cfg(tmp_path, tmp_mailbox, transport="icloud", inbox_list_id="ck-1")
    setup_mod.write_config(cfg_file, transport="radicale")
    setup_mod.write_config(cfg_file, transport="radicale", overrides={"inbox_list_id": "rad-1"})

    assert load_config(cfg_file).inbox_list_id == "rad-1", "ids from the CURRENT transport stay"

    setup_mod.write_config(cfg_file, transport="icloud")
    assert load_config(cfg_file).inbox_list_id == "", "and are dropped on the way back"


def test_staying_on_the_same_transport_touches_nothing(tmp_path, tmp_mailbox):
    """A re-run of setup is not a switch. Clearing a selection because someone
    pressed Enter twice would be its own defect."""
    cfg_file = _write_cfg(tmp_path, tmp_mailbox, transport="icloud", inbox_list_id="ck-1")

    notes = setup_mod.write_config(cfg_file, transport="icloud")

    assert notes == []
    assert load_config(cfg_file).inbox_list_id == "ck-1"


def test_a_first_run_is_not_a_switch(tmp_path, tmp_mailbox):
    """No previous transport means nothing was switched away from."""
    cfg_file = tmp_path / "voice-bridge.json"

    notes = setup_mod.write_config(
        cfg_file, transport="icloud", overrides={"mailbox_dir": str(tmp_mailbox)}
    )

    assert notes == []


# --------------------------------------------------------------------------- #
# 1c - what doctor says about the hybrid that already exists
# --------------------------------------------------------------------------- #


def test_doctor_flags_ids_that_the_current_transport_cannot_resolve(
    tmp_path, tmp_mailbox, fake_transport, monkeypatch
):
    """Asked explicitly: does doctor catch the hybrid, or is that a sub-defect?

    It catches it. The ids resolve against the CURRENT backend's inventory, so
    CloudKit ids under radicale come back `stale` and the row is WARN with
    "run `voice-bridge lists --select`" - the same remedy the switch now states up
    front. Worth a test because the answer was not obvious from reading either
    module, and because doctor keying off `cfg.transport` is what setup failed to do.
    """
    cfg_file = _write_cfg(
        tmp_path,
        tmp_mailbox,
        transport="radicale",
        inbox_list_id="B1CC-cloudkit",
        output_list_id="825C-cloudkit",
    )
    rows = doctor_mod._list_rows(load_config(cfg_file), fake_transport)
    name, verdict, detail = rows[1]

    assert (name, verdict) == ("lists", "WARN")
    assert "no longer exists" in detail
    assert "lists --select" in detail


# --------------------------------------------------------------------------- #
# 2 - a closed set that is actually closed
# --------------------------------------------------------------------------- #


def test_an_invalid_transport_is_re_asked_not_accepted(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """They typed `rad` and were told "ACCEPTED - transport = rad".

    The prompt PRINTS its choices, so accepting anything else contradicts the same
    line the user is reading. `[Y/n]` has re-asked junk since batch 3; this is that
    rule reaching the one other prompt with a closed set.
    """
    answers = iter(["rad", "radicale"])
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    # Keyed on the FIELD, not on the prompt's prose - which is what `_ask`'s field
    # argument is for. Matching the prose first cost a confusing failure here: the
    # mailbox prompt echoes a tmp path, and this test's own name was in that path,
    # so "transport" matched a directory and ate an answer.
    monkeypatch.setattr(
        setup_mod,
        "_ask",
        lambda field, label, default: next(answers) if field == "transport" else "",
    )

    cfg = load_config(_write_cfg(tmp_path, tmp_mailbox))
    got = setup_mod.prompt_fields(cfg, preset={})
    out = capsys.readouterr().out

    assert got["transport"] == "radicale"
    assert "not one of the choices" in out
    assert "ACCEPTED - transport = rad\n" not in out, "no acceptance of a rejected value"
    assert out.count("ACCEPTED - transport") == 1


def test_free_text_fields_are_still_free(tmp_path, tmp_mailbox, monkeypatch):
    """Validation belongs to the fields that HAVE a closed set. A spoke may be
    called anything."""
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(
        setup_mod, "_ask", lambda field, label, default: "Parrot" if field == "spoke_name" else ""
    )

    got = setup_mod.prompt_fields(load_config(_write_cfg(tmp_path, tmp_mailbox)), preset={})

    assert got == {"spoke_name": "Parrot"}


def test_the_factory_refuses_an_unknown_transport(tmp_path, tmp_mailbox):
    """The half that input validation would have left alive.

    `make_transport` fell through to iCloud for ANY unrecognised value, so a typo in
    a hand-edited config did not fail - it silently selected the other backend. That
    is the nothing-is-ever-chosen-for-you rule one layer below the pickers.
    """
    cfg = load_config(_write_cfg(tmp_path, tmp_mailbox, transport="rad"))

    with pytest.raises(NotSupportedError) as exc:
        factory.make_transport(cfg)

    assert "rad" in str(exc.value)
    assert "icloud" in str(exc.value) and "radicale" in str(exc.value)


def test_the_closed_set_lives_where_the_adapters_are_named():
    """One list. A second copy in setup.py would drift the day a transport is added."""
    assert factory.TRANSPORTS == ("icloud", "radicale")
    assert all(
        field[2] is factory.TRANSPORTS for field in setup_mod._PROMPTABLE if field[0] == "transport"
    )


# --------------------------------------------------------------------------- #
# 3 - the probe title: FAIL and verified cannot both be true
# --------------------------------------------------------------------------- #


class _Rec:
    def __init__(self, fields) -> None:
        self.fields = fields


class _Raw:
    def __init__(self, records) -> None:
        self._records = records

    def lookup(self, **_kw):
        return type("R", (), {"records": self._records})()


class _Svc:
    """Enough of pyicloud's private read path for `check_stored` to walk."""

    def __init__(self, records) -> None:
        self._reads = type("Reads", (), {"_get_raw": lambda _self: _Raw(records)})()


def _verdict(records):
    from voice_bridge.titlelint import check_stored

    return check_stored(_Svc(records), "abc-123")


def test_a_record_that_was_not_readable_yet_is_UNDETERMINED():
    """CloudKit answers a lookup it cannot serve with an entry carrying no fields,
    and a record written a moment ago is exactly what it cannot serve yet. That fell
    into "no TitleDocument on the stored record" - asserting the record exists
    WITHOUT a title, which we never observed."""
    v = _verdict([_Rec(None)])

    assert v.ok is False
    assert v.undetermined is True
    assert "not readable yet" in v.describe()
    assert "NOT renderable" not in v.describe()


def test_no_records_at_all_is_also_undetermined():
    v = _verdict([])

    assert v.undetermined is True


def test_a_record_we_DID_read_without_a_title_is_a_real_finding():
    """The claim the old message made, now made only when it is earned: a record
    present and titleless is what a blank reminder looks like from here."""
    v = _verdict([_Rec({"Title": {"value": "x"}})])

    assert v.ok is False
    assert v.undetermined is False
    assert "carries no TitleDocument" in v.describe()


def test_a_lookup_that_raises_is_undetermined_not_a_failure():
    from voice_bridge.titlelint import check_stored

    class Boom:
        @property
        def _reads(self):
            raise RuntimeError("network")

    v = check_stored(Boom(), "abc")

    assert v.undetermined is True


def test_verify_never_prints_FAIL_and_verified_together(
    tmp_path, tmp_mailbox, monkeypatch, capsys, fake_transport
):
    """The transcript that produced this item:

        [ok  ] notification sent
        [FAIL] the phone will not render that title - NOT renderable: '' - ...
        verified: a message makes the round trip.

    Whatever the truth was, that output cannot be right. A proven-bad title is a
    message the phone cannot show, so it joins the contract legs and blocks.
    """
    cfg_file = _write_cfg(
        tmp_path, tmp_mailbox, inbox_list_id="i1", output_list_id="o1", transport="icloud"
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(setup_mod, "make_transport", lambda _cfg: fake_transport)
    monkeypatch.setattr(setup_mod, "settle_selection", lambda *a, **k: 0)
    monkeypatch.setattr(
        setup_mod,
        "verify",
        lambda *_a, **_k: {
            "dictation_delivered": True,
            "reply_delivered": True,
            "banner_sent": True,
            "banner_detail": "sent",
            "title_renderable": False,
            "title_detail": "NOT renderable: '' - no TitleDocument",
        },
    )

    rc = setup_mod.run_setup(config_path=cfg_file, do_verify=True)
    out = capsys.readouterr().out

    assert rc == 2
    assert "[FAIL] the phone will not render" in out
    assert "verified: a message makes the round trip." not in out
    assert "the title will not render" in out


def test_an_unconfirmed_title_reports_unknown_and_blocks_nothing(
    tmp_path, tmp_mailbox, monkeypatch, capsys, fake_transport
):
    """The other half of the ruling. A diagnostic that could not reach a verdict has
    not found a defect - and the round trip it was checking DID complete, which is
    the claim the user came for."""
    cfg_file = _write_cfg(
        tmp_path, tmp_mailbox, inbox_list_id="i1", output_list_id="o1", transport="icloud"
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(setup_mod, "make_transport", lambda _cfg: fake_transport)
    monkeypatch.setattr(setup_mod, "settle_selection", lambda *a, **k: 0)
    monkeypatch.setattr(
        setup_mod,
        "verify",
        lambda *_a, **_k: {
            "dictation_delivered": True,
            "reply_delivered": True,
            "banner_sent": True,
            "banner_detail": "sent",
            "title_renderable": None,
            "title_detail": "could not be checked - the record was not readable yet",
        },
    )

    rc = setup_mod.run_setup(config_path=cfg_file, do_verify=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert "[ ?  ] title not confirmed" in out
    assert "[FAIL]" not in out
    assert "verified: a message makes the round trip." in out


def test_a_transport_with_no_stored_documents_says_not_applicable(tmp_path, tmp_mailbox):
    """Unchanged from batch 4, and re-pinned because `None` now carries a second
    meaning: "not applicable" and "could not check" are both unknown, and neither
    may read as a pass."""
    renderable, detail = setup_mod._check_title(object(), "id")

    assert renderable is None
    assert "not applicable" in detail


# --------------------------------------------------------------------------- #
# 4 - "(just created)" is a claim about the mailbox
# --------------------------------------------------------------------------- #


def test_created_ness_is_measured_on_the_mailbox_not_on_the_files(tmp_path, monkeypatch, capsys):
    """It printed on EVERY run of a live session, back-to-back runs included.

    A peer that ejects deletes its own inbox file, so the next start legitimately
    re-touches one - and the flag came back true. An event that fires every time is
    a state wearing an event's clothes, which is the inverse of the law this block
    was written to satisfy.
    """
    # NOT the `tmp_mailbox` fixture: it creates the directory, which would make the
    # first run a non-creation and quietly invert what this test is checking.
    fresh = tmp_path / "fresh-mail"
    cfg_file = _write_cfg(
        tmp_path, fresh, inbox_list_id="i1", output_list_id="o1", transport="icloud"
    )
    monkeypatch.setattr(runner.poller, "run", lambda *a, **k: 0)
    monkeypatch.setattr(runner, "make_transport", lambda _cfg: object())

    runner.run_command(config_path=cfg_file, once=True)
    first = capsys.readouterr().out

    # A peer ejects: its inbox file goes, the mailbox does not.
    load_config(cfg_file).our_inbox.unlink()
    runner.run_command(config_path=cfg_file, once=True)
    second = capsys.readouterr().out

    assert "(just created)" in first, "the first run really did create it"
    assert "(just created)" not in second, "re-touching a message file is not a new mailbox"
    assert str(fresh) in second, "the mailbox line still prints, without the claim"


# --------------------------------------------------------------------------- #
# 5 - a check that is a check
# --------------------------------------------------------------------------- #


def _passing_result(**over):
    base = {
        "dictation_delivered": True,
        "reply_delivered": True,
        "banner_sent": True,
        "banner_detail": "sent",
        "title_renderable": True,
        "title_detail": "renderable: 'probe'",
    }
    base.update(over)
    return base


def test_the_verify_verb_runs_no_flow_at_all(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """Asked from a phone: "What is the purpose of setup --verify? It seems unclear
    and confusing."

    It was doing what it said - a setup, with a verification inside it - and the name
    promised the reverse. Someone asking "does my bridge still work?" answered five
    guided steps to reach one probe. The verb asks nothing: if a prompt happens here
    this test fails rather than hanging.
    """
    cfg_file = _write_cfg(
        tmp_path, tmp_mailbox, inbox_list_id="i1", output_list_id="o1", transport="icloud"
    )

    def _never(prompt: str) -> str:
        raise AssertionError(f"a check must not ask questions (asked {prompt!r})")

    monkeypatch.setattr(setup_mod, "_ask_line", _never)
    monkeypatch.setattr(setup_mod, "verify", lambda *_a, **_k: _passing_result())

    rc = setup_mod.verify_command(load_config(cfg_file), lambda _cfg: object())
    out = capsys.readouterr().out

    assert rc == 0
    assert "verified: a message makes the round trip." in out
    for ceremony in ("== YOUR LISTS ==", "Press Enter to keep", "Credentials", "phone prompt"):
        assert ceremony not in out, f"the check walked part of the flow: {ceremony!r}"


def test_the_verify_verb_writes_no_config(tmp_path, tmp_mailbox, monkeypatch):
    """`setup --verify` on a fresh machine WRITES a config, because writing one is
    setup's job. A check must not - the RUN-4 rule for `--dry-run`, applied to the
    other command people reach for when they only want to look."""
    cfg_file = _write_cfg(
        tmp_path, tmp_mailbox, inbox_list_id="i1", output_list_id="o1", transport="icloud"
    )
    before = cfg_file.read_text(encoding="utf-8")
    monkeypatch.setattr(
        setup_mod,
        "verify",
        lambda *_a, **_k: _passing_result(title_renderable=None, title_detail=""),
    )

    setup_mod.verify_command(load_config(cfg_file), lambda _cfg: object())

    assert cfg_file.read_text(encoding="utf-8") == before


def test_both_doors_print_one_report(tmp_path, tmp_mailbox, monkeypatch, capsys, fake_transport):
    """The verb and the flag share `report_verification`, so the FAIL-inside-verified
    defect cannot come back through only one of them."""
    cfg_file = _write_cfg(
        tmp_path, tmp_mailbox, inbox_list_id="i1", output_list_id="o1", transport="icloud"
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(setup_mod, "make_transport", lambda _cfg: fake_transport)
    monkeypatch.setattr(setup_mod, "settle_selection", lambda *a, **k: 0)
    monkeypatch.setattr(
        setup_mod,
        "verify",
        lambda *_a, **_k: _passing_result(
            title_renderable=False, title_detail="NOT renderable: ''"
        ),
    )

    via_verb = setup_mod.verify_command(load_config(cfg_file), lambda _cfg: fake_transport)
    verb_out = capsys.readouterr().out
    via_flag = setup_mod.run_setup(config_path=cfg_file, do_verify=True)
    flag_out = capsys.readouterr().out

    assert via_verb == via_flag == 2
    assert "the title will not render" in verb_out
    assert "the title will not render" in flag_out


def test_setups_verify_help_admits_it_runs_the_whole_flow():
    """The flag was not lying about itself; it was silent about the ceremony around
    it. However that is resolved, the help must not read as "just a check"."""
    import argparse

    from voice_bridge import cli

    parser = cli._build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    verify_flag = next(a for a in sub.choices["setup"]._actions if a.option_strings == ["--verify"])

    assert "full guided setup" in verify_flag.help
    assert "verify" in sub.choices["setup"].epilog, "point at the standalone verb"
    assert "verify" in sub.choices, "the standalone verb exists"


# --------------------------------------------------------------------------- #
# 6 - the flag audit
# --------------------------------------------------------------------------- #


def _all_flags():
    """Every flag on every command, read from the parser rather than from memory."""
    import argparse

    from voice_bridge import cli

    parser = cli._build_parser()
    out = []
    for act in parser._actions:
        if not isinstance(act, argparse._SubParsersAction):
            continue
        for name, sub in act.choices.items():
            for a in sub._actions:
                if isinstance(a, argparse._SubParsersAction):
                    for n2, s2 in a.choices.items():
                        out += [
                            (f"{name} {n2}", x)
                            for x in s2._actions
                            if x.option_strings and x.option_strings != ["-h", "--help"]
                        ]
                elif a.option_strings and a.option_strings != ["-h", "--help"]:
                    out.append((name, a))
    return out


def test_every_flag_says_what_it_does():
    """The audit's own gate.

    Twenty-one flags shipped with NO help text - the strongest form of "the help does
    not say what happens", because the user cannot find out at all without reading
    source. A flag is a promise; an unexplained one is a promise in a language nobody
    was given.
    """
    silent = [
        f"{cmd} {'/'.join(a.option_strings)}"
        for cmd, a in _all_flags()
        if not (a.help or "").strip()
    ]

    assert not silent, "flags with no help: " + ", ".join(silent)


def test_json_names_the_commands_it_actually_changes():
    from voice_bridge import cli

    parser = cli._build_parser()
    json_flag = next(a for a in parser._actions if a.option_strings == ["--json"])

    assert "where supported" not in json_flag.help, "a hedge is not a location"
    for cmd in cli._JSON_COMMANDS:
        assert cmd in json_flag.help


def test_json_on_a_command_that_ignores_it_says_so(monkeypatch, capsys):
    """A script that passes --json and receives prose had no way to know. Said on
    stderr, where it cannot corrupt the output being parsed."""
    from voice_bridge import cli

    monkeypatch.setattr(cli, "path_note", lambda: "")
    cli.main(["--json", "config", "fields"])
    captured = capsys.readouterr()

    # `config fields` specifically: `config show --json` DOES work, so the note has to
    # be finer than command level or it becomes the very thing it was added to prevent.
    assert "--json has no effect on `config fields`" in captured.err
    assert "--json" not in captured.out


def test_json_on_a_supporting_command_stays_quiet(monkeypatch, capsys):
    from voice_bridge import cli

    monkeypatch.setattr(cli, "path_note", lambda: "")
    monkeypatch.setattr(cli.status_mod, "gather", lambda _cfg: {"ok": True})
    cli.main(["--json", "status"])

    assert "no effect" not in capsys.readouterr().err


def test_the_transport_choices_are_one_list_not_three():
    """Found while auditing something else: `cli._TRANSPORTS` was a third copy of a
    set `factory` already owned and setup's preamble already imported."""
    from voice_bridge import cli, factory

    assert cli._TRANSPORTS is factory.TRANSPORTS


def test_the_two_flags_whose_help_understated_the_consequence():
    """`radicale-server init --force` said "rotate existing credentials" while also
    rewriting the server config and the bcrypt user file - after which the phone stops
    connecting until its CalDAV account is updated. `deliver --public` publishes to a
    searchable page. Both consequences now sit in the help, where the person typing
    the flag is looking."""
    flags = {f"{cmd} {'/'.join(a.option_strings)}": (a.help or "") for cmd, a in _all_flags()}

    assert "phone" in flags["radicale-server init --force"]
    assert "PUBLICLY" in flags["deliver --public"]


def test_verify_touches_no_network_when_it_was_never_going_to_work(tmp_path, tmp_mailbox, capsys):
    """Found by running the new verb once, for real, at the gated ref.

    With no lists selected it printed `connecting to iCloud...` and THEN refused. The
    precondition is offline and free; authenticating first means a command that could
    not possibly do its job still reaches somebody's account. That is the batch-4
    ordering rule - which `run` already obeys - and the reason `verify_command` takes
    a factory rather than a live transport.

    This one also has teeth beyond tidiness: the account here belongs to a person, and
    a check is the command most likely to be run idly.
    """
    cfg_file = _write_cfg(tmp_path, tmp_mailbox, transport="icloud")  # no ids selected

    def _never(_cfg):
        raise AssertionError("connected before checking a precondition that needs no network")

    rc = setup_mod.verify_command(load_config(cfg_file), _never)
    out = capsys.readouterr().out

    assert rc == 2
    assert "No list is selected" in out


def test_the_cli_passes_the_factory_rather_than_a_connection():
    """The seam has to survive at the call site too - building the transport in `cli`
    would restore the defect while every test here still passed."""
    import ast

    from voice_bridge import cli

    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "verify_command"
    ]
    assert calls, "the verb must still be wired"
    for call in calls:
        for arg in call.args[1:]:
            assert not isinstance(arg, ast.Call), (
                f"cli.py:{call.lineno} connects before the offline gate runs"
            )


# --------------------------------------------------------------------------- #
# 6b - the note that exists to stop --json lying, lying
# --------------------------------------------------------------------------- #


def test_every_command_in_the_json_set_really_emits_json(monkeypatch, capsys):
    """Over-claim guard, and the defect that produced it in reverse.

    `_JSON_COMMANDS` shipped without `config`, so `--json config show` printed
    "no effect on `config`" about a path that has always worked - the json branch
    lives inside `_config_cmd`, not in the dispatch table I read to build the list.
    CI was green on that; nothing had asked whether the list was right.
    """
    import json as _json

    from voice_bridge import cli

    monkeypatch.setattr(cli, "path_note", lambda: "")
    monkeypatch.setattr(cli.status_mod, "gather", lambda _cfg: {"probe": 1})

    # `config show` and `status` are the two that need no transport; the rest are
    # covered by their own command tests. This checks the CLAIM, not the formatting.
    for argv in (["--json", "status"], ["--json", "config", "show"]):
        cli.main(argv)
        captured = capsys.readouterr()
        assert "no effect" not in captured.err, f"{argv} is in the set but was disclaimed"
        payload = captured.out.strip().splitlines()[-1]
        _json.loads(payload)  # raises if it was prose


def test_no_json_path_hides_outside_the_declared_set():
    """Under-claim guard - the direction that actually bit.

    Every read of `args.json` in `cli.py` must sit in a branch or helper belonging to
    a command in `_JSON_COMMANDS`. Helpers count: `_config_cmd` is where the missing
    one was, and reading only the dispatch table is how it stayed missing.
    """
    import ast

    from voice_bridge import cli

    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))

    def _reads_json(node) -> bool:
        return any(
            isinstance(n, ast.Attribute)
            and n.attr == "json"
            and isinstance(n.value, ast.Name)
            and n.value.id == "args"
            for n in ast.walk(node)
        )

    unclaimed: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        # A helper named `_<command>_cmd` belongs to that command.
        helper = fn.name[1:-4] if fn.name.startswith("_") and fn.name.endswith("_cmd") else None
        if helper and _reads_json(fn) and helper not in cli._JSON_COMMANDS:
            unclaimed.append(f"{fn.name} reads args.json but '{helper}' is not declared")
        if helper:
            continue
        # Otherwise: dispatch-style `if cmd == "name":` blocks.
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "cmd"
                and isinstance(test.comparators[0], ast.Constant)
            ):
                continue
            name = test.comparators[0].value
            if _reads_json(node) and name not in cli._JSON_COMMANDS:
                unclaimed.append(f"`{name}` reads args.json but is not declared")

    assert not unclaimed, "; ".join(unclaimed)
