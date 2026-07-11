"""Shared iCloud/Radicale-CalDAV plumbing for the voice-bridge alt transport.

Network + credential handling lives here so `probe.py` and `reminder_bridge.py`
stay small and share ONE connection/discovery path. No secrets are ever printed:
`Creds.masked()` is the only representation meant for logs.

This is the ALT transport (the primary is `pyicloud_bridge.py`). Use it when the
CloudKit private-API path is unavailable and you run a self-hosted Radicale
CalDAV server both the phone and PC can see (see radicale/OWNER-SETUP.md).

Contract:
  - CalDAV root defaults to https://caldav.icloud.com/ ; ICLOUD_CALDAV_URL
    overrides it (point at Radicale). The client discovers the pNN shard on iCloud.
  - Basic Auth = username + password. On iCloud that password MUST be an
    app-specific password (iCloud has no OAuth); on Radicale it's the Radicale user.
  - reminder lists are VTODO-capable calendar collections; reminders are VTODO.

List names come from the resolved voice-bridge config (inbox_list / output_list).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Config

CALDAV_URL = "https://caldav.icloud.com/"


class CredsError(RuntimeError):
    """Credentials are missing or malformed."""


@dataclass(frozen=True)
class Creds:
    apple_id: str
    app_password: str

    def masked(self) -> str:
        """Log-safe view. NEVER exposes the app-specific password."""
        local, _, domain = self.apple_id.partition("@")
        who = f"{local[:1]}***@{domain}" if domain else "***"
        return f"apple_id={who} app_password=<{len(self.app_password)} chars, hidden>"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        out[key.strip()] = val
    return out


def load_creds(cfg: Config) -> Creds:
    """Read creds from the environment first, then the config's creds_env file.

    Env: ICLOUD_APPLE_ID (or ICLOUD_USERNAME) + ICLOUD_APP_PASSWORD.
    File (cfg.creds_env): the same keys as `KEY=value` lines.
    Also surfaces an optional ICLOUD_CALDAV_URL override (the Radicale fallback).
    """
    apple_id = (os.environ.get("ICLOUD_APPLE_ID") or os.environ.get("ICLOUD_USERNAME") or "").strip()
    app_password = (os.environ.get("ICLOUD_APP_PASSWORD") or "").strip()

    path = cfg.creds_env
    if path.exists():
        data = _parse_env_file(path)
        apple_id = apple_id or (data.get("ICLOUD_APPLE_ID") or data.get("ICLOUD_USERNAME") or "").strip()
        app_password = app_password or (data.get("ICLOUD_APP_PASSWORD") or "").strip()
        if not os.environ.get("ICLOUD_CALDAV_URL") and data.get("ICLOUD_CALDAV_URL"):
            os.environ["ICLOUD_CALDAV_URL"] = data["ICLOUD_CALDAV_URL"].strip()

    missing = [
        name
        for name, val in (("ICLOUD_APPLE_ID", apple_id), ("ICLOUD_APP_PASSWORD", app_password))
        if not val
    ]
    if missing:
        raise CredsError(
            "Missing credential(s): "
            + ", ".join(missing)
            + f". Set them in the environment or in {path}. "
            "On iCloud the password must be an APP-SPECIFIC password from "
            "appleid.apple.com (not your Apple ID password); on Radicale it is the "
            "Radicale user's password."
        )
    return Creds(apple_id=apple_id, app_password=app_password)


def caldav_url() -> str:
    """The CalDAV root. Defaults to iCloud; ICLOUD_CALDAV_URL overrides it so the
    same code can point at a self-hosted Radicale server (the CloudKit fallback)."""
    return (os.environ.get("ICLOUD_CALDAV_URL") or CALDAV_URL).strip()


def connect(creds: Creds, *, timeout: float = 30.0) -> Any:
    """Return the CalDAV principal (for iCloud, follows the redirect to the pNN shard).

    Raises a clean message on an auth failure so a RED probe can name the mode.
    """
    from caldav import DAVClient
    from caldav.lib.error import AuthorizationError, DAVError

    client = DAVClient(
        url=caldav_url(),
        username=creds.apple_id,
        password=creds.app_password,
        timeout=timeout,
    )
    try:
        return client.principal()
    except AuthorizationError as exc:
        raise CredsError(
            "server rejected the credentials (401). Verify ICLOUD_APPLE_ID and that "
            "ICLOUD_APP_PASSWORD is CURRENT (on iCloud, app-specific passwords are "
            "revoked when the Apple ID password changes)."
        ) from exc
    except DAVError as exc:  # pragma: no cover - network
        raise CredsError(f"CalDAV discovery failed: {exc}") from exc


def _display_name(cal: Any) -> str:
    try:
        name = cal.get_display_name()
    except Exception:
        name = None
    if name:
        return str(name)
    return str(cal.url).rstrip("/").rsplit("/", 1)[-1]


def todo_lists(principal: Any) -> list[tuple[str, Any]]:
    """Every VTODO-capable collection as (display_name, calendar).

    A missing component set means "supports all" per RFC 4791, so it is kept;
    only an explicitly events-only collection is skipped.
    """
    out: list[tuple[str, Any]] = []
    for cal in principal.calendars():
        try:
            comps = cal.get_supported_components()
        except Exception:
            comps = []
        if comps and "VTODO" not in comps:
            continue
        out.append((_display_name(cal), cal))
    return out


def find_list(principal: Any, name: str) -> Any | None:
    """The VTODO calendar whose display name matches `name` (case-insensitive)."""
    target = name.strip().lower()
    for disp, cal in todo_lists(principal):
        if disp.strip().lower() == target:
            return cal
    return None


def todo_fields(todo: Any) -> dict[str, str | None]:
    """Pull (uid, title, notes) out of a caldav Todo via its ical component."""
    comp = todo.icalendar_component

    def _get(key: str) -> str | None:
        val = comp.get(key)
        return str(val) if val is not None else None

    return {"uid": _get("uid"), "title": _get("summary"), "notes": _get("description")}
