"""Command functions that take an already-connected transport.

The standing principle behind this module: **no backend calls inline in the CLI
handler**. Logic that lives in `cli.py` is neither error-disciplined nor testable,
and the same root produced four different behaviours for one backend failure —
`lists` and `peek` raised a traceback, `send` returned a blanket exit 2, and
`setup.provision` reported a genuine auth failure as "create the lists by hand".

`connected_transport` gives all four one mapping; the `*_command` functions take
the transport as an argument so they can be driven by a fake.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge.caldav import CredsError
from voice_bridge.commands import connected_transport, list_command, peek_command
from voice_bridge.errors import CommandError
from voice_bridge.icloud import ICloudError


# --------------------------------------------------------------------------- #
# TRANSPORT-1 — one typed mapping for every command that touches a backend
# --------------------------------------------------------------------------- #

def test_missing_credentials_becomes_exit_1_with_its_message(sample_config, boom_transport):
    """Not a traceback: the user needs to be told to finish setting up."""
    t = boom_transport(ICloudError("Missing ICLOUD_APPLE_ID / ICLOUD_PASSWORD in creds.env"))
    with pytest.raises(CommandError) as err:
        connected_transport(sample_config, make=lambda cfg: t)
    assert err.value.code == 1
    assert "ICLOUD_APPLE_ID" in err.value.msg, "the original message must survive"


def test_caldav_401_becomes_exit_1_not_a_traceback(sample_config, boom_transport):
    t = boom_transport(CredsError("server rejected the credentials (401)."))
    with pytest.raises(CommandError) as err:
        connected_transport(sample_config, make=lambda cfg: t)
    assert err.value.code == 1
    assert "401" in err.value.msg


def test_missing_list_becomes_exit_2_with_guidance(sample_config, boom_transport):
    t = boom_transport(LookupError("no list named 'Vox-Message-Inbox'"))
    with pytest.raises(CommandError) as err:
        connected_transport(sample_config, make=lambda cfg: t)
    assert err.value.code == 2
    assert "Vox-Message-Inbox" in err.value.msg


def test_unexpected_error_is_reraised_not_swallowed(sample_config, boom_transport):
    """A bug must keep its traceback; a tidy exit code would hide it."""
    boom = RuntimeError("attribute error deep in the transport")
    t = boom_transport(boom)
    with pytest.raises(RuntimeError) as err:
        connected_transport(sample_config, make=lambda cfg: t)
    assert err.value is boom


def test_happy_path_returns_a_connected_transport(sample_config, fake_transport):
    t = connected_transport(sample_config, make=lambda cfg: fake_transport)
    assert t is fake_transport
    assert fake_transport.connected is True


# --------------------------------------------------------------------------- #
# LIST-2 — ghosts, and saying which list is actually in use
# --------------------------------------------------------------------------- #

def _cfg_with(sample_config, **kw):
    import dataclasses

    return dataclasses.replace(sample_config, **kw)


def test_pinned_id_marks_only_the_pinned_ghost(sample_config, ghost_transport, capsys):
    """Two lists share the name; only the pinned id is the one in use."""
    cfg = _cfg_with(sample_config, inbox_list_id="L9")
    assert list_command(cfg, ghost_transport, as_json=False) == 0
    out = capsys.readouterr().out
    active = [ln for ln in out.splitlines() if "L9" in ln or "active" in ln]
    assert active, "the pinned list must be marked"
    assert "L1" in out, "the ghost must still be listed so the user can see it"


def test_dangling_pin_warns(sample_config, ghost_transport, capsys):
    """A pin pointing at a deleted list is a silent breakage otherwise."""
    cfg = _cfg_with(sample_config, inbox_list_id="GONE")
    list_command(cfg, ghost_transport, as_json=False)
    out = capsys.readouterr().out.lower()
    assert "warn" in out or "not found" in out


def test_without_a_pin_name_matching_is_preserved(sample_config, fake_transport, capsys):
    list_command(sample_config, fake_transport, as_json=False)
    out = capsys.readouterr().out
    assert "Vox-Message-Inbox" in out


def test_json_rows_carry_id_role_and_active(sample_config, ghost_transport, capsys):
    cfg = _cfg_with(sample_config, inbox_list_id="L9")
    list_command(cfg, ghost_transport, as_json=True)
    rows = json.loads(capsys.readouterr().out)
    assert {"name", "id", "role", "active"} <= set(rows[0])
    pinned = [r for r in rows if r["id"] == "L9"][0]
    assert pinned["active"] is True
    assert pinned["role"] == "inbox"
    ghost = [r for r in rows if r["id"] == "L1"][0]
    assert ghost["active"] is False


# --------------------------------------------------------------------------- #
# PEEK-2/3/4 — limits, ids, and an honest empty state
# --------------------------------------------------------------------------- #

def _seed(cfg, t, n=3):
    lst = t.resolve_list(cfg.inbox_list, "")
    for i in range(n):
        t.add_todo(lst, f"dictation {i}", notes=f"notes {i}")
    return lst


def test_peek_limit_zero_shows_nothing(sample_config, fake_transport, capsys):
    """`-n 0` meant "no limit" because 0 is falsy — it must mean zero."""
    _seed(sample_config, fake_transport)
    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=0,
                 as_json=True)
    assert json.loads(capsys.readouterr().out) == []


def test_peek_limit_two_and_unset(sample_config, fake_transport, capsys):
    _seed(sample_config, fake_transport, 3)
    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=2,
                 as_json=True)
    assert len(json.loads(capsys.readouterr().out)) == 2

    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=None,
                 as_json=True)
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_peek_json_includes_id_and_needs_input(sample_config, fake_transport, capsys):
    """Without the id you cannot act on what you just looked at."""
    _seed(sample_config, fake_transport, 1)
    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=None,
                 as_json=True)
    row = json.loads(capsys.readouterr().out)[0]
    assert {"id", "title", "notes", "needs_input"} <= set(row)


def test_peek_human_output_shows_the_id(sample_config, fake_transport, capsys):
    _seed(sample_config, fake_transport, 1)
    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=None,
                 as_json=False)
    assert "item-1" in capsys.readouterr().out


def test_peek_empty_box_says_so(sample_config, fake_transport, capsys):
    """An empty box printed nothing at all, which reads as a broken command."""
    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=None,
                 as_json=False)
    assert "no items" in capsys.readouterr().out.lower()

    peek_command(sample_config, fake_transport, box="inbox", completed=False, limit=None,
                 as_json=True)
    assert json.loads(capsys.readouterr().out) == []


def test_peek_missing_list_raises_command_error(sample_config, fake_transport):
    cfg = _cfg_with(sample_config, inbox_list="Nope")
    with pytest.raises(CommandError) as err:
        peek_command(cfg, fake_transport, box="inbox", completed=False, limit=None, as_json=False)
    assert err.value.code == 2


def test_the_factory_seam_is_resolved_at_call_time(sample_config, fake_transport, monkeypatch):
    """Patching `commands.make_transport` must actually intercept.

    Writing `make: Callable = make_transport` as a default argument binds the
    function object at import time, so a later monkeypatch of the module
    attribute is silently ignored and the real backend is used anyway — the seam
    looks present but is not connected to anything.
    """
    import voice_bridge.commands as commands_mod

    monkeypatch.setattr(commands_mod, "make_transport", lambda cfg: fake_transport)
    assert connected_transport(sample_config) is fake_transport
