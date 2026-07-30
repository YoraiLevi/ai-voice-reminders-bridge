"""`doctor` — a survey that must never report a false GREEN.

A health check is the one command whose *only* product is justified confidence,
so every way it can be wrong is a way it makes things worse than having no check
at all. The version this replaces could report GREEN for five different untrue
reasons:

* the config row was a hardcoded GREEN, printed before anything was inspected;
* the credentials row asked only whether the file *existed*, so an empty or
  half-written one passed;
* the lists row matched on name, so a pinned id pointing at a deleted list passed
  while every poll would fail;
* the topic row passed on an empty file, and an empty topic silently disables
  notifications;
* the mailbox row **created the directory it was checking**, so it reported on a
  state it had just manufactured.

Every test here is therefore a "must not be GREEN when X" test.
"""

from __future__ import annotations

import dataclasses

from voice_bridge import doctor as doctor_mod
from voice_bridge.util import write_env


def _rows(capsys) -> dict[str, str]:
    """Parse the printed survey back into {row name: status}."""
    out = {}
    for line in capsys.readouterr().out.splitlines():
        if "[" in line and "]" in line:
            status = line.split("[", 1)[1].split("]", 1)[0].strip()
            name = line.split("]", 1)[1].split("->")[0].strip()
            out[name] = status
    return out


def _healthy_creds(cfg):
    write_env(cfg.creds_env, {"ICLOUD_APPLE_ID": "me@icloud.com", "ICLOUD_PASSWORD": "pw"})


# --------------------------------------------------------------------------- #
# DOCTOR-4 — the survey must observe, not mutate
# --------------------------------------------------------------------------- #


def test_survey_does_not_create_the_mailbox_it_is_checking(sample_config, fake_transport, capsys):
    """Reporting on a directory you just created is not a check.

    It also destroys the diagnosis: "the mailbox is missing" is exactly what a
    user with a misconfigured `mailbox_dir` needs to be told.
    """
    missing = sample_config.mailbox_dir / "definitely-not-here"
    cfg = dataclasses.replace(
        sample_config,
        mailbox_dir=missing,
        peer_inbox=missing / "to-manager.md",
        our_inbox=missing / "to-vox.md",
    )
    doctor_mod.run(cfg, fake_transport)
    assert not missing.exists(), "the survey must not create anything"
    assert _rows(capsys)["mailbox dir"] != "GREEN"


def test_fix_may_create_the_mailbox(sample_config, fake_transport, capsys):
    """`--fix` is the consent that makes mutation legitimate."""
    missing = sample_config.mailbox_dir / "made-by-fix"
    cfg = dataclasses.replace(
        sample_config,
        mailbox_dir=missing,
        peer_inbox=missing / "to-manager.md",
        our_inbox=missing / "to-vox.md",
    )
    doctor_mod.run(cfg, fake_transport, fix=True)
    assert missing.exists()


# --------------------------------------------------------------------------- #
# DOCTOR-1 — credentials: present is not the same as usable
# --------------------------------------------------------------------------- #


def test_empty_creds_file_is_not_green(sample_config, fake_transport, capsys):
    sample_config.creds_env.parent.mkdir(parents=True, exist_ok=True)
    sample_config.creds_env.write_text("", encoding="utf-8")
    doctor_mod.run(sample_config, fake_transport)
    assert _rows(capsys)["creds file"] != "GREEN"


def test_creds_missing_a_required_key_is_not_green(sample_config, fake_transport, capsys):
    write_env(sample_config.creds_env, {"ICLOUD_APPLE_ID": "me@icloud.com"})  # no password
    doctor_mod.run(sample_config, fake_transport)
    assert _rows(capsys)["creds file"] != "GREEN"


def test_complete_creds_are_green(sample_config, fake_transport, capsys):
    _healthy_creds(sample_config)
    doctor_mod.run(sample_config, fake_transport)
    assert _rows(capsys)["creds file"] == "GREEN"


def test_quote_wrapped_password_is_flagged(sample_config, fake_transport, capsys):
    """The doctor twin of the login warning, so the diagnosis exists in both places."""
    write_env(
        sample_config.creds_env,
        {"ICLOUD_APPLE_ID": "me@icloud.com", "ICLOUD_PASSWORD": '"wrapped"'},
    )
    doctor_mod.run(sample_config, fake_transport)
    assert "quote" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# DOCTOR-2 — a pinned id that points at nothing must not pass
# --------------------------------------------------------------------------- #


def test_dangling_pin_is_not_green(sample_config, fake_transport, capsys):
    """Name matching hid this: the name exists, so the row went GREEN — while
    every poll would fail, because polling resolves by the pinned id."""
    cfg = dataclasses.replace(sample_config, inbox_list_id="NO-SUCH-ID")
    _healthy_creds(cfg)
    doctor_mod.run(cfg, fake_transport)
    assert _rows(capsys)["lists"] != "GREEN"


def test_both_roles_selected_is_green(sample_config, fake_transport, capsys):
    """GREEN now requires BOTH roles to be selected by id.

    It used to pass with neither, because an unset id fell back to matching the
    name — so the row reported healthy about a list nobody had chosen.
    """
    inbox = fake_transport.resolve_list("Vox-Message-Outbox", "")
    outbox = fake_transport.resolve_list("Vox-Message-Inbox", "")
    cfg = dataclasses.replace(sample_config, inbox_list_id=inbox.id, output_list_id=outbox.id)
    _healthy_creds(cfg)
    doctor_mod.run(cfg, fake_transport)
    assert _rows(capsys)["lists"] == "GREEN"


def test_an_unselected_role_is_not_green(unselected_config, fake_transport, capsys):
    """Nothing is polled for a role with no id, so silence here would be a lie."""
    inbox = fake_transport.resolve_list("Vox-Message-Outbox", "")
    cfg = dataclasses.replace(unselected_config, inbox_list_id=inbox.id)  # outbox unset
    _healthy_creds(cfg)
    doctor_mod.run(cfg, fake_transport)
    assert _rows(capsys)["lists"] != "GREEN"


def test_unpinned_ghost_holding_items_warns(sample_config, ghost_transport, capsys):
    """Two lists share the name and one has messages in it — resolve-by-name is a
    coin flip, and the losing side means dictations that are never seen."""
    lst = [r for r in ghost_transport.list_todo_lists() if r.id == "L9"][0]
    ghost_transport.add_todo(lst, "a dictation nobody will read")
    _healthy_creds(sample_config)
    doctor_mod.run(sample_config, ghost_transport)
    assert _rows(capsys)["lists"] != "GREEN"


# --------------------------------------------------------------------------- #
# DOCTOR-3 — an empty topic file silently disables notifications
# --------------------------------------------------------------------------- #


def test_empty_topic_file_is_not_green(sample_config, fake_transport, capsys):
    sample_config.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
    sample_config.ntfy_topic_file.write_text("   \n", encoding="utf-8")
    doctor_mod.run(sample_config, fake_transport)
    assert _rows(capsys)["ntfy topic"] != "GREEN"


def test_non_empty_topic_is_green(sample_config, fake_transport, capsys):
    sample_config.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
    sample_config.ntfy_topic_file.write_text("my-secret-topic", encoding="utf-8")
    doctor_mod.run(sample_config, fake_transport)
    assert _rows(capsys)["ntfy topic"] == "GREEN"


# --------------------------------------------------------------------------- #
# DOCTOR-5 — the config row must report something it actually looked at
# --------------------------------------------------------------------------- #


def test_config_row_names_its_source(sample_config, fake_transport, capsys):
    """It was a hardcoded GREEN — true by construction, informative about nothing."""
    doctor_mod.run(sample_config, fake_transport)
    out = capsys.readouterr().out
    assert "config" in out
    assert sample_config.source in out or "defaults" in out


# --------------------------------------------------------------------------- #
# exit code — the worst row wins, so a script can gate on it
# --------------------------------------------------------------------------- #


def test_exit_code_is_the_worst_row(sample_config, fake_transport, capsys):
    inbox = fake_transport.resolve_list("Vox-Message-Outbox", "")
    outbox = fake_transport.resolve_list("Vox-Message-Inbox", "")
    sample_config = dataclasses.replace(
        sample_config, inbox_list_id=inbox.id, output_list_id=outbox.id
    )
    _healthy_creds(sample_config)
    sample_config.mailbox_dir.mkdir(parents=True, exist_ok=True)
    sample_config.peer_inbox.touch()
    sample_config.our_inbox.touch()
    sample_config.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
    sample_config.ntfy_topic_file.write_text("t", encoding="utf-8")
    assert doctor_mod.run(sample_config, fake_transport) == 0

    sample_config.creds_env.write_text("", encoding="utf-8")  # now RED
    assert doctor_mod.run(sample_config, fake_transport) == 2


def test_fix_clears_a_dangling_pin_rather_than_re_resolving_it(
    unselected_config, ghost_transport, capsys
):
    """A dead pin means the list you chose is gone.

    Re-resolving by name would hand back a DIFFERENT list under a repair verb — a
    silent substitution disguised as a fix. Clearing is honest: it restores the
    "we don't know yet" state, and the next interactive run asks properly.
    """
    import dataclasses
    import json
    from pathlib import Path

    cfg = dataclasses.replace(unselected_config, inbox_list_id="GONE")
    doctor_mod.run(cfg, ghost_transport, fix=True)

    written = json.loads(Path(cfg.source).read_text(encoding="utf-8"))
    assert written["inbox_list_id"] == "", "the dead pin must be cleared"
    assert written["inbox_list_id"] not in ("L1", "L9"), "it must NOT silently re-resolve"
    assert "cleared" in capsys.readouterr().out


def test_the_survey_alone_never_clears_a_pin(unselected_config, ghost_transport, capsys):
    """Read-only means read-only; --fix is the consent."""
    import dataclasses
    import json
    from pathlib import Path

    cfg = dataclasses.replace(unselected_config, inbox_list_id="GONE")
    doctor_mod.run(cfg, ghost_transport)

    written = json.loads(Path(cfg.source).read_text(encoding="utf-8"))
    assert "inbox_list_id" not in written
    assert "lists --select" in capsys.readouterr().out, "it must name the way to re-pin"


def test_case_twin_lists_are_reported_as_a_conflict(sample_config, capsys):
    """Exact-name matching found precisely one and reported GREEN — the false-GREEN
    class this module exists to purge."""
    from voice_bridge.transport import FakeTransport

    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "T1")
    t.add_list("vox-message-outbox", "T2")
    t.add_list("Vox-Message-Inbox", "T3")

    doctor_mod.run(sample_config, t)
    assert _rows(capsys)["lists"] != "GREEN"


def test_environment_credentials_are_reported_because_they_win(
    sample_config, fake_transport, capsys, monkeypatch
):
    """Found in a QA rehearsal, and it cost an hour of confusion.

    `radicale-server init` writes credentials and prints where it put them, so the
    user reasonably believes those are in use. An inherited ICLOUD_APPLE_ID
    silently authenticated as someone else and the only symptom was a bare 401.

    This docstring used to end: *"the precedence is deliberate and stays - scripted
    runs depend on env vars. What was wrong was that nothing said so."* **The field
    disagreed.** In act 5 the identical confusion happened to the human on a live run:
    `ICLOUD_*` in their shell authenticated the RADICALE transport with their Apple
    password, and the only symptom was a 401 - again.

    So the rehearsal diagnosed the mechanism correctly and chose the smaller fix, report
    it. Reporting is not a fix when the reader is a person who did not export the
    variable on purpose. Batch 15 changed the precedence: a creds file written FOR this
    transport now outranks an env var named for a different one, and this row's wording
    follows the behaviour rather than describing the old one.

    Same shape as FMA-14 - correct analysis, deferred remedy, and the field arriving to
    collect. The row still WARNS, because credentials that are being ignored are worth
    knowing about; what changed is which sentence is true.
    """
    _healthy_creds(sample_config)
    monkeypatch.setenv("ICLOUD_APPLE_ID", "someone-else")

    doctor_mod.run(sample_config, fake_transport)
    out = capsys.readouterr().out

    assert "ICLOUD_APPLE_ID" in out
    assert "environment" in out
    assert "WIN over" not in out, "that claim is no longer true on either transport"


def test_no_environment_credentials_means_no_such_warning(
    sample_config, fake_transport, capsys, monkeypatch
):
    """It must not cry wolf on a clean shell."""
    for key in (
        "ICLOUD_APPLE_ID",
        "ICLOUD_USERNAME",
        "ICLOUD_APP_PASSWORD",
        "ICLOUD_PASSWORD",
        "ICLOUD_CALDAV_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    _healthy_creds(sample_config)

    doctor_mod.run(sample_config, fake_transport)
    assert "WIN over" not in capsys.readouterr().out
