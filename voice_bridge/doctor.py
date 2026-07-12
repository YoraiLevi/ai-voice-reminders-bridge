"""`doctor` — survey every interface, report GREEN/WARN/RED with a fix per line.
Folds in probe.py's checks and extends them across the whole setup. Returns the worst
severity as the exit code (0 GREEN / 1 WARN / 2 RED)."""

from __future__ import annotations

from .config import Config
from .factory import make_transport
from .transport import Transport

_RANK = {"GREEN": 0, "WARN": 1, "RED": 2}


def run(cfg: Config, t: Transport | None = None, *, fix: bool = False) -> int:
    t = t or make_transport(cfg)
    rows: list[tuple[str, str, str]] = [("config", "GREEN", "")]

    if cfg.creds_env.exists():
        rows.append(("creds file", "GREEN", ""))
    else:
        rows.append(("creds file", "RED", f"create {cfg.creds_env}"))

    auth, lists, fixmsg = "GREEN", "GREEN", ""
    try:
        t.connect()
        names = {r.name for r in t.list_todo_lists()}
        missing = [n for n in (cfg.inbox_list, cfg.output_list) if n not in names]
        if missing:
            lists = "WARN"
            fixmsg = f"missing {missing} — run `voice-bridge setup`"
    except Exception as exc:
        auth = "RED"
        fixmsg = f"{type(exc).__name__}: {exc}"
    rows.append(("transport auth", auth, fixmsg if auth == "RED" else ""))
    rows.append(("lists", lists, fixmsg if lists == "WARN" else ""))

    if cfg.ntfy_topic_file.exists():
        rows.append(("ntfy topic", "GREEN", ""))
    else:
        rows.append(("ntfy topic", "WARN", f"write a topic to {cfg.ntfy_topic_file}"))

    mbx = "GREEN"
    try:
        cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
        if fix:
            cfg.peer_inbox.touch(exist_ok=True)
            cfg.our_inbox.touch(exist_ok=True)
    except Exception:
        mbx = "RED"
    rows.append(("mailbox dir", mbx, "" if mbx == "GREEN" else f"cannot write {cfg.mailbox_dir}"))

    worst = 0
    for name, status, msg in rows:
        line = f"  [{status:<5}] {name}"
        if msg:
            line += f"  -> {msg}"
        print(line)
        worst = max(worst, _RANK[status])
    return worst
