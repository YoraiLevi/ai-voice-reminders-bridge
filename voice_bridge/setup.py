"""Provisioning: write the config and (Radicale) create the two lists, or (iCloud)
print the manual list-creation step. Folds in bootstrap.py's provisioning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config, default_config_path, load_config, read_raw, write_raw
from .factory import make_transport
from .transport import NotSupportedError, Transport


def write_config(path: Path, *, transport: str, overrides: dict[str, Any] | None = None) -> None:
    """Merge transport + overrides into the config file (create it if absent)."""
    data = read_raw(path)
    data["transport"] = transport
    data.update(overrides or {})
    write_raw(path, data)


def provision(cfg: Config, t: Transport) -> list[str]:
    """Ensure the two lists exist. Radicale creates them; iCloud reports the manual
    step. Returns human-readable status lines."""
    if cfg.transport == "radicale":
        t.connect()
        for name in (cfg.inbox_list, cfg.output_list):
            t.create_list(name)
        return [
            f"Radicale: created / verified {cfg.inbox_list!r} and {cfg.output_list!r}.",
            "They sync to the phone via the shared CalDAV account — no phone step.",
        ]
    try:
        t.connect()
        t.resolve_list(cfg.inbox_list, cfg.inbox_list_id)
        t.resolve_list(cfg.output_list, cfg.output_list_id)
        return [f"iCloud: both lists visible ({cfg.inbox_list!r} / {cfg.output_list!r})."]
    except Exception:
        return [
            "iCloud can't create lists over the API. On the phone Reminders app,",
            f"create TWO lists named EXACTLY:  {cfg.inbox_list}   {cfg.output_list}",
            f"SETUP_DONE lists_needed={cfg.inbox_list}|{cfg.output_list}",
        ]


def run_setup(
    *,
    config_path: str | Path | None = None,
    transport: str = "icloud",
    overrides: dict | None = None,
) -> int:
    """Write config + provision. Returns 0, or 2 on a guard error."""
    path = Path(config_path) if config_path else default_config_path()
    write_config(path, transport=transport, overrides=overrides)
    cfg = load_config(path)
    t = make_transport(cfg)
    try:
        for line in provision(cfg, t):
            print(line)
    except NotSupportedError as exc:
        print(f"error: {exc}")
        return 2
    return 0
