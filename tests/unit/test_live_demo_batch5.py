"""A crash, from the human's hands, with the traceback in the manager's custody.

`lists --select`, role 1, picked 21 - and then a post-pick re-read hit a real
iCloud slowdown. pyicloud raised `PyiCloudAPIResponseException` for a 60-second
read timeout, it escaped RAW because this path had no transient classification at
all, and **the choice was lost with it**.

Three separate failures met in one place, which is why it is worth writing down:

1. `setup` grew careful transient handling in batch 2 and `lists --select` never
   did - one implementation and one GAP, and the gap was where a person stood.
2. Batch 3 then ADDED a network call to that unprotected path.
3. The call was unnecessary. "21" means row 21 of the screen in front of the user;
   re-fetching the inventory to interpret it answers a different question.

The human's own words on (3), asked unprompted after reading the crash: *"why does
it take another query to select the list?"* It does not, any more.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import commands, selection, transient
from voice_bridge.config import load_config
from voice_bridge.errors import is_transient
from voice_bridge.transport import FakeTransport, ListRef


def _cfg(tmp_path, tmp_mailbox, **extra):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state"), **extra}
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file), cfg_file


class _Stalling(FakeTransport):
    """A transport that times out the way pyicloud does: a wrapper type that keeps
    the words and loses the exception class."""

    def __init__(self, fail_times: int = 1) -> None:
        super().__init__()
        self.left = fail_times
        self.fetches = 0

    def list_todo_lists(self):
        self.fetches += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError(
                "ckdatabasews.icloud.com: HTTPSConnectionPool read timed out. (read timeout=60.0)"
            )
        return super().list_todo_lists()


# --------------------------------------------------------------------------- #
# the classification that never ran
# --------------------------------------------------------------------------- #


def test_a_wrapped_read_timeout_is_transient():
    """pyicloud wraps a read timeout in `PyiCloudAPIResponseException`, which is
    neither a requests type nor a throttle - so the classifier called it permanent
    and it escaped as a traceback. Text is the last resort, after type and cause."""
    assert is_transient(RuntimeError("HTTPSConnectionPool: read timed out. (read timeout=60.0)"))


def test_a_timeout_reached_through_context_is_transient():
    """A wrapper that re-raises inside an `except` block WITHOUT `from` still
    records what it was handling. A library is under no obligation to chain."""
    import requests

    try:
        try:
            raise requests.Timeout("read timed out")
        except requests.Timeout:
            raise RuntimeError("Request failed") from None
    except RuntimeError as exc:
        exc.__context__ = requests.Timeout("read timed out")
        assert is_transient(exc)


def test_an_auth_failure_that_mentions_a_timeout_is_still_not_transient():
    """The auth verdict comes before every text sniff, or "your session timed out"
    becomes an invitation to retry something that cannot work."""
    from voice_bridge.icloud import ICloudError

    assert not is_transient(ICloudError("session timed out - re-run icloud-login"))


# --------------------------------------------------------------------------- #
# 1 - one implementation, shared
# --------------------------------------------------------------------------- #


def test_offer_retry_refuses_to_dress_a_real_error_as_a_blip(sample_config):
    """ "Want to try again?" for an auth failure is the mis-advice class wearing a
    friendly face - it invites the user to repeat something that cannot work."""
    from voice_bridge.icloud import ICloudError

    with pytest.raises(ICloudError):
        transient.offer_retry(
            ICloudError("bad password"),
            sample_config,
            ask=lambda _p: "y",
            show=lambda _s: None,
            is_tty=lambda: True,
        )


def test_offer_retry_never_asks_without_a_terminal(sample_config):
    import requests

    asked = []
    out: list[str] = []
    result = transient.offer_retry(
        requests.Timeout("read timed out"),
        sample_config,
        ask=lambda p: asked.append(p) or "y",
        show=out.append,
        is_tty=lambda: False,
    )

    assert result is False
    assert asked == [], "a prompt to a pipe blocks forever or takes an answer nobody gave"
    assert any("temporary failure" in line for line in out)


# --------------------------------------------------------------------------- #
# 2 - a blip must not cost an answer
# --------------------------------------------------------------------------- #


def test_a_stall_at_the_opening_menu_offers_a_retry_instead_of_crashing(tmp_path, tmp_mailbox):
    """This path had NO transient handling. The whole batch starts here."""
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox)
    t = _Stalling(fail_times=1)
    t.add_list("Groceries")
    answers = iter(["y", "d"])  # yes retry, then done
    out: list[str] = []

    rc = commands.select_command(
        cfg,
        t,
        config_path=cfg_file,
        ask=lambda _p: next(answers),
        show=out.append,
        is_tty=lambda: True,
    )
    text = "\n".join(out)

    assert rc == 0
    assert "temporary failure" in text
    assert "Traceback" not in text
    assert t.fetches == 2, "it retried the failed read"


def test_declining_the_retry_leaves_without_a_traceback(tmp_path, tmp_mailbox):
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox)
    t = _Stalling(fail_times=99)
    out: list[str] = []

    rc = commands.select_command(
        cfg, t, config_path=cfg_file, ask=lambda _p: "n", show=out.append, is_tty=lambda: True
    )
    assert rc == 1
    assert "temporary failure" in "\n".join(out)


# --------------------------------------------------------------------------- #
# 4 - the pick resolves against the screen it was made on (RULED)
# --------------------------------------------------------------------------- #


def test_choosing_a_row_costs_no_extra_network_call(tmp_path, tmp_mailbox):
    """Ruled from the human's own question: "why does it take another query to
    select the list?"

    The role menu and the picker have already read the account. A third read could
    only agree with what the user saw, disagree with it, or - as happened - time
    out after the decision and lose it.
    """
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox)
    t = _Stalling(fail_times=0)
    t.add_list("Groceries")
    t.add_list("Vox-Out")
    answers = iter(["1", "2", "d"])  # role 1, row 2, done
    out: list[str] = []

    commands.select_command(
        cfg,
        t,
        config_path=cfg_file,
        ask=lambda _p: next(answers),
        show=out.append,
        is_tty=lambda: True,
    )

    assert t.fetches == 1, f"one inventory read for the whole interaction, got {t.fetches}"
    written = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert written["inbox_list_id"] == "list-vox-out"
    assert written["inbox_list"] == "Vox-Out"


def test_the_picker_hands_back_the_row_the_user_saw():
    """`Choice.ref` is the exact row, and `seen` is the screen it came from."""
    refs = [ListRef(id="a", name="Alpha"), ListRef(id="b", name="Beta")]
    res = selection.Resolution("inbox", "", "inbox_list_id", "", "unselected", refs, "")
    choice = selection.pick(res, ask=lambda _p: "2", show=lambda _s: None)

    assert choice.action == "select"
    assert choice.ref is not None and choice.ref.name == "Beta"
    assert choice.seen == refs


def test_a_refreshed_screen_is_what_the_choice_resolves_against():
    """`r) refresh` exists so a list made seconds ago can be picked. The row the
    user then chooses belongs to the REFRESHED screen, not the opening one - which
    is exactly the batch-3 bug, now fixed without a network call."""
    opening = [ListRef(id="a", name="Alpha")]
    after = [*opening, ListRef(id="new", name="Made While You Waited")]
    res = selection.Resolution("inbox", "", "inbox_list_id", "", "unselected", opening, "")

    answers = iter(["r", "2"])
    choice = selection.pick(
        res, ask=lambda _p: next(answers), show=lambda _s: None, refresh=lambda: after
    )

    assert choice.ref is not None and choice.ref.id == "new"
    assert choice.seen == after, "the snapshot travels with the answer"


# --------------------------------------------------------------------------- #
# 3 - the budget applies to the calls that actually hang
# --------------------------------------------------------------------------- #


def test_an_explicit_timeout_is_clamped_to_the_budget():
    """The first version yielded to an explicit timeout, which sounds correct and
    had the exact wrong effect: pyicloud's cloudkit client passes timeout=60, so
    our 30s budget applied to everything EXCEPT the calls that hang."""
    from voice_bridge.icloud import _apply_timeout

    seen = {}

    class _Session:
        def request(self, *a, **kw):
            seen.update(kw)

    api = type("Api", (), {"session": _Session()})()
    _apply_timeout(api, 30)
    api.session.request("GET", "u", timeout=60.0)

    assert seen["timeout"] == 30.0


def test_a_shorter_explicit_timeout_is_left_alone():
    """Clamping is `min`, not `override`: a caller asking for LESS knows something
    we do not."""
    from voice_bridge.icloud import _apply_timeout

    seen = {}

    class _Session:
        def request(self, *a, **kw):
            seen.update(kw)

    api = type("Api", (), {"session": _Session()})()
    _apply_timeout(api, 30)
    api.session.request("GET", "u", timeout=5)

    assert seen["timeout"] == 5.0


def test_a_connect_read_pair_stays_a_pair():
    """`requests` accepts a (connect, read) tuple, and silently turning it into a
    scalar is how a fix becomes a new bug."""
    from voice_bridge.icloud import _apply_timeout

    seen = {}

    class _Session:
        def request(self, *a, **kw):
            seen.update(kw)

    api = type("Api", (), {"session": _Session()})()
    _apply_timeout(api, 30)
    api.session.request("GET", "u", timeout=(5, 60))

    assert seen["timeout"] == (5.0, 30.0)


# --------------------------------------------------------------------------- #
# the footnote - the lists step was the only silent one
# --------------------------------------------------------------------------- #


def test_the_lists_step_says_what_is_already_chosen(tmp_path, tmp_mailbox, fake_transport):
    """Steps 1 and 3 report "already configured: <path>" on a re-run. This one
    printed its header and nothing else - the only step whose state the user
    actually cares about was the only silent one."""
    from voice_bridge import setup as setup_mod

    ref = fake_transport.list_todo_lists()[0]
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox, inbox_list_id=ref.id, output_list_id=ref.id)
    out: list[str] = []

    setup_mod.settle_selection(
        cfg, fake_transport, config_path=cfg_file, show=out.append, is_tty=lambda: True
    )
    text = "\n".join(out)

    assert "already chosen" in text
    assert ref.name in text
    assert ref.id in text
