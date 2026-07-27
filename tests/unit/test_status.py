"""status.gather: file/pidfile/reachability glance (no transport auth)."""

from __future__ import annotations

from voice_bridge import status


def test_gather_down_system(sample_config):
    d = status.gather(sample_config)
    assert d["transport"] == "icloud"
    assert d["poller_running"] is False
    assert d["last_dictation"] is None and d["last_reply"] is None
    assert d["server_reachable"] is None  # not radicale
    assert d["inbox_seen"] == 0


def test_gather_reports_the_last_dictation(sample_config):
    sample_config.peer_inbox.parent.mkdir(parents=True, exist_ok=True)
    sample_config.peer_inbox.write_text("- [08:48] (vox) hi\n", encoding="utf-8")
    assert status.gather(sample_config)["last_dictation"] is not None


# --------------------------------------------------------------------------- #
# STATUS-1 — the labels were inverted
# --------------------------------------------------------------------------- #

def test_field_names_describe_the_direction_they_report(sample_config):
    """`last_inbound` read the file we WRITE, and `last_outbound` the one we READ.

    The peer's inbox (`to-manager.md`) is where our dictations go out; our own
    inbox (`to-vox.md`) is where replies come in. So both labels named the
    opposite of what they measured — an operator debugging "nothing is arriving"
    was looking at the wrong timestamp entirely.
    """
    sample_config.mailbox_dir.mkdir(parents=True, exist_ok=True)
    sample_config.peer_inbox.write_text("- [10:00] (vox) a dictation\n", encoding="utf-8")

    data = status.gather(sample_config)
    assert "last_dictation" in data
    assert "last_reply" in data
    assert "last_inbound" not in data and "last_outbound" not in data
    assert data["last_dictation"] is not None, "we wrote a dictation, so it must be stamped"
    assert data["last_reply"] is None, "nothing has replied to us"


def test_reply_timestamp_tracks_our_own_inbox(sample_config):
    sample_config.mailbox_dir.mkdir(parents=True, exist_ok=True)
    sample_config.our_inbox.write_text("- [10:01] (manager) ok\n", encoding="utf-8")
    data = status.gather(sample_config)
    assert data["last_reply"] is not None
    assert data["last_dictation"] is None


# --------------------------------------------------------------------------- #
# FMA-17 — a non-positive pid must never reach os.kill
# --------------------------------------------------------------------------- #

def test_non_positive_pid_is_never_probed(sample_config, monkeypatch):
    """`os.kill(0, 0)` does not ask "is pid 0 alive?" on Windows.

    `signal.CTRL_C_EVENT` is 0 and pid 0 means "every process in this group", so
    the liveness probe would deliver a real Ctrl-C to the whole group — the
    status command killing the poller it was asked to report on. A corrupt or
    empty pidfile is enough to reach it.
    """
    called = []
    monkeypatch.setattr(status.os, "kill", lambda pid, sig: called.append(pid))

    for bad in (0, -1, -12345):
        assert status._alive(bad) is False
    assert called == [], "a non-positive pid must be rejected before any signal call"


def test_positive_pid_still_probes(sample_config, monkeypatch):
    monkeypatch.setattr(status.os, "kill", lambda pid, sig: None)
    assert status._alive(4242) is True


def test_corrupt_pidfile_reports_not_running(sample_config):
    status.poller_pidfile(sample_config).parent.mkdir(parents=True, exist_ok=True)
    status.poller_pidfile(sample_config).write_text("0", encoding="utf-8")
    assert status.poller_pid(sample_config) is None


# --------------------------------------------------------------------------- #
# FMA-9 — say when nothing is reading, without inventing peer liveness
# --------------------------------------------------------------------------- #

def _stamp(path, text, when):
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (when, when))


def test_hint_fires_when_a_dictation_goes_unanswered(sample_config):
    """The silent half-round-trip: delivered correctly, and nobody is listening.

    voice-bridge cannot see whether a peer exists, so it reports the file facts it
    CAN see rather than guessing — an unanswered dictation older than the
    threshold, with the peer's file untouched since.
    """
    from voice_bridge.mailbox import format_mailbox_line

    _stamp(sample_config.peer_inbox, format_mailbox_line("buy milk", from_name="vox") + "\n", 2000)
    _stamp(sample_config.our_inbox, "", 1000)
    hint = status.staleness_hint(sample_config, stale_after=60, now=5000)
    assert hint is not None
    assert "no newer reply" in hint


def test_hint_is_quiet_when_a_reply_is_newer(sample_config):
    from voice_bridge.mailbox import format_mailbox_line

    _stamp(sample_config.peer_inbox, format_mailbox_line("buy milk", from_name="vox") + "\n", 1000)
    _stamp(sample_config.our_inbox, "- [10:01] (manager) ok\n", 3000)
    assert status.staleness_hint(sample_config, stale_after=60, now=5000) is None


def test_hint_is_quiet_within_the_threshold(sample_config):
    from voice_bridge.mailbox import format_mailbox_line

    _stamp(sample_config.peer_inbox, format_mailbox_line("buy milk", from_name="vox") + "\n", 4990)
    _stamp(sample_config.our_inbox, "", 1000)
    assert status.staleness_hint(sample_config, stale_after=3600, now=5000) is None


def test_our_own_join_announcement_does_not_look_like_a_dictation(sample_config):
    """The discriminator is FORMAT, not authorship.

    EVERY line in the peer's file is our write — dictations are forwarded under
    our tag — so "is this ours?" distinguishes nothing and would silence the hint
    for ever. A join/eject announcement is not an unanswered question.
    """
    from voice_bridge.mailbox import join_line

    _stamp(sample_config.peer_inbox, join_line("vox") + "\n", 2000)
    _stamp(sample_config.our_inbox, "", 1000)
    assert status.staleness_hint(sample_config, stale_after=60, now=5000) is None


def test_hint_is_disabled_by_negative_threshold(sample_config):
    from voice_bridge.mailbox import format_mailbox_line

    _stamp(sample_config.peer_inbox, format_mailbox_line("x", from_name="vox") + "\n", 2000)
    _stamp(sample_config.our_inbox, "", 1000)
    assert status.staleness_hint(sample_config, stale_after=-1, now=5000) is None


def test_missing_our_inbox_counts_as_never_answered(sample_config):
    """After a clean eject our inbox is deleted; a stale dictation must still fire."""
    from voice_bridge.mailbox import format_mailbox_line

    _stamp(sample_config.peer_inbox, format_mailbox_line("x", from_name="vox") + "\n", 2000)
    if sample_config.our_inbox.exists():
        sample_config.our_inbox.unlink()
    assert status.staleness_hint(sample_config, stale_after=60, now=5000) is not None
