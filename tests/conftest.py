"""Shared fixtures. The FakeTransport + a fixed clock are the highest-leverage ones:
they let the whole spoke be tested with no network and deterministic timestamps.
"""

from __future__ import annotations

import json
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import pytest

from voice_bridge.config import load_config
from voice_bridge.transport import FakeTransport


@pytest.fixture(autouse=True)
def _clean_icloud_env(monkeypatch):
    """Keep host ICLOUD_* env vars from leaking into file-based creds resolution."""
    for k in (
        "ICLOUD_APPLE_ID",
        "ICLOUD_USERNAME",
        "ICLOUD_APP_PASSWORD",
        "ICLOUD_PASSWORD",
        "ICLOUD_CALDAV_URL",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fixed_clock() -> datetime:
    """A frozen wall-clock for deterministic [HH:MM] stamps."""
    return datetime(2026, 7, 12, 8, 48, 0)


@pytest.fixture
def tmp_mailbox(tmp_path: Path) -> Path:
    """An empty mailbox dir (the shared bus)."""
    d = tmp_path / "agent-mail"
    d.mkdir()
    return d


@pytest.fixture
def sample_config(tmp_path: Path, tmp_mailbox: Path):
    """A resolved Config whose mailbox + state dirs live under tmp (no touching $HOME)."""
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


@pytest.fixture
def fake_transport() -> FakeTransport:
    """In-memory backend pre-seeded with the two default lists."""
    t = FakeTransport()
    t.add_list("Vox-Message-Inbox")
    t.add_list("Vox-Message-Outbox")
    return t


@pytest.fixture
def radicale_server(tmp_path):
    """An embedded Radicale CalDAV server on a random localhost port — the real oracle
    for the CalDAV transport. htpasswd auth (test:test), tmp filesystem storage."""
    import socket
    import threading
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    radicale = pytest.importorskip("radicale")
    from radicale import config as rconfig

    storage = tmp_path / "collections"
    storage.mkdir()
    users = tmp_path / "users"
    users.write_text("test:test\n", encoding="utf-8")
    cfg = rconfig.load(())
    cfg.update(
        {
            "auth": {
                "type": "htpasswd",
                "htpasswd_filename": str(users),
                "htpasswd_encryption": "plain",
            },
            "storage": {"filesystem_folder": str(storage)},
            "rights": {"type": "authenticated"},
        },
        "test",
        privileged=True,
    )
    app = radicale.Application(cfg)

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    class _Quiet(WSGIRequestHandler):
        def log_message(self, *a):  # noqa: D401 - silence access log
            pass

    httpd = make_server("127.0.0.1", port, app, handler_class=_Quiet)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "username": "test", "password": "test"}
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def radicale_config(radicale_server, tmp_path, tmp_mailbox):
    """A resolved Config wired to the embedded Radicale server (transport=radicale)."""
    import json

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    creds = state / "radicale.env"
    creds.write_text(
        f"ICLOUD_APPLE_ID={radicale_server['username']}\n"
        f"ICLOUD_APP_PASSWORD={radicale_server['password']}\n"
        f"ICLOUD_CALDAV_URL={radicale_server['url']}\n",
        encoding="utf-8",
    )
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(state),
                "creds_env": str(creds),
            }
        ),
        encoding="utf-8",
    )
    from voice_bridge.config import load_config

    return load_config(cfg_file)


@dataclass
class BoomTransport(FakeTransport):
    """A transport whose `connect()` fails, to drive the typed-error mapping.

    The point of the mapping is that *which* error surfaces decides the exit code
    and whether the user is told to fix credentials, fix a list name, or file a
    bug — so the tests need to choose the failure.
    """

    boom: Exception | None = None

    def connect(self) -> None:
        if self.boom is not None:
            raise self.boom
        super().connect()


@pytest.fixture
def boom_transport():
    """Factory: `boom_transport(SomeError("x"))` -> a transport that fails to connect."""
    return lambda exc: BoomTransport(boom=exc)


@pytest.fixture
def ghost_transport() -> FakeTransport:
    """Two lists sharing the inbox NAME with different ids — a real iCloud hazard.

    Deleting and recreating a list on the phone leaves same-titled orphans, so
    resolve-by-name silently picks one at random. This is exactly what pinning an
    id exists to disambiguate, and `lists` must make the ambiguity visible.
    """
    t = FakeTransport()
    t.add_list("Vox-Message-Inbox", "L1")  # the ghost (older, empty)
    t.add_list("Vox-Message-Outbox", "L2")
    t.add_list("Vox-Message-Inbox", "L9")  # the live one
    return t


class FakeICloudService:
    """Stands in for `PyiCloudService`, modelling only the surface login uses.

    It RECORDS its calls, which is what turns LOGIN-4 into a regression sentinel:
    delete `trust_session()` from the implementation and the durability test goes
    red, rather than the session quietly lapsing weeks later on a real phone.

    No test imports pyicloud — the same stance `test_icloud.py` takes.
    """

    def __init__(
        self,
        apple_id: str,
        password: str,
        cookie_directory: str | None = None,
        *,
        good_password: str = "correct-horse",
        mfa_required: bool = True,
        hsa_version: int = 2,
        code_ok: bool = True,
        trust_works: bool = True,
    ) -> None:
        if password != good_password:
            raise RuntimeError("Invalid email/password combination.")
        self.apple_id = apple_id
        self.password = password
        self.cookie_directory = cookie_directory
        # Model the account the way pyicloud does — one MFA flag and an hsaVersion —
        # rather than two independent booleans. The flags below are DERIVED, so this
        # double cannot express a combination the real library never produces. It
        # previously could, and that is exactly how LIVE-1 escaped: every test set
        # `requires_2sa` independently of `requires_2fa`, while in reality a modern
        # two-factor account reports BOTH as true.
        self._mfa = mfa_required
        self._hsa = hsa_version
        self.is_trusted_session = not self.requires_2fa
        self._code_ok = code_ok
        self._trust_works = trust_works
        self.calls: list[str] = ["construct"]

    @property
    def requires_2sa(self) -> bool:
        """`hsaVersion >= 1` — a SUPERSET, true for legacy 2SA *and* modern 2FA."""
        return self._mfa and self._hsa >= 1

    @property
    def requires_2fa(self) -> bool:
        """`hsaVersion == 2` — modern two-factor only."""
        return self._mfa and self._hsa == 2

    def validate_2fa_code(self, code: str) -> bool:
        self.calls.append(f"validate_2fa_code:{code}")
        if self._code_ok:
            self._mfa = False  # satisfied: both derived flags fall together
        return self._code_ok

    def trust_session(self) -> None:
        self.calls.append("trust_session")
        if self._trust_works:
            self.is_trusted_session = True


@pytest.fixture
def fake_icloud(monkeypatch):
    """Install a `FakeICloudService` behind `login._make_service`; return a handle.

    `_make_service` is the ONE injection point for the whole login flow, so a test
    never needs the network, a real Apple ID, or pyicloud.
    """
    from voice_bridge import login as login_mod

    state: dict = {"service": None, "kwargs": {}}

    def install(**kwargs):
        state["kwargs"] = kwargs

        def _make(apple_id, password, cookie_dir):
            svc = FakeICloudService(apple_id, password, str(cookie_dir), **state["kwargs"])
            state["service"] = svc
            return svc

        monkeypatch.setattr(login_mod, "_make_service", _make)
        return state

    install()  # sensible defaults; a test may re-install with its own
    return install
