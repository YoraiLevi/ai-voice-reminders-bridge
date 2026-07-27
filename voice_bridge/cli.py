"""The `voice-bridge` command — argparse subcommands over the package. Uniform exit
codes: 0 success · 1 soft-negative · 2 usage/config/guard error."""

from __future__ import annotations

import argparse
import json
import sys

from . import deliver as deliver_mod
from . import doctor as doctor_mod
from . import log as log_mod
from . import login as login_mod
from . import ntfy
from . import poller as poller_mod
from . import prompt as prompt_mod
from . import server as server_mod
from . import setup as setup_mod
from . import status as status_mod
from .config import (
    ConfigError,
    field_help,
    load_config,
    parse_overrides,
    resolve_config_path,
    set_value,
)
from .errors import CommandError
from .factory import make_transport
from .runner import run_command

_TRANSPORTS = ("icloud", "radicale")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voice-bridge", description="A voice spoke for a file-mailbox."
    )
    p.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json")
    p.add_argument("-v", "--verbose", action="store_true", help="INFO logging")
    p.add_argument("-q", "--quiet", action="store_true", help="errors only")
    p.add_argument("--log-file", metavar="PATH", help="also log to this file")
    p.add_argument("--json", action="store_true", help="machine-readable output where supported")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="ensure everything, then bridge (the main command)")
    r.add_argument("--mailbox")
    r.add_argument("--transport", choices=_TRANSPORTS)
    r.add_argument("--require-mailbox", action="store_true")
    r.add_argument("--interval", type=int)
    r.add_argument("--once", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument(
        "--with-server", action="store_true", help="(radicale) spawn the server as a child"
    )
    r.add_argument("--set", action="append", dest="overrides", metavar="KEY=VALUE")

    s = sub.add_parser("setup", help="provision the transport (config + lists)")
    s.add_argument("--transport", choices=_TRANSPORTS, default="icloud")
    s.add_argument("--set", action="append", dest="overrides", metavar="KEY=VALUE")

    d = sub.add_parser("doctor", help="survey the setup; --fix repairs safe items")
    d.add_argument("--fix", action="store_true")

    sub.add_parser("lists", help="enumerate transport lists with their ids")

    pk = sub.add_parser("peek", help="show messages in a box")
    pk.add_argument("--box", choices=("inbox", "outbox"), default="inbox")
    pk.add_argument("--completed", action="store_true")
    pk.add_argument("-n", type=int, dest="limit")

    n = sub.add_parser("notify", help="push a phone banner")
    n.add_argument("text")
    n.add_argument("--click", metavar="URL")

    c = sub.add_parser("config", help="show/get/set settings")
    csub = c.add_subparsers(dest="op")
    csub.add_parser("show")
    csub.add_parser("fields", help="list every settable field and its default")
    cg = csub.add_parser("get")
    cg.add_argument("key")
    cs = csub.add_parser("set")
    cs.add_argument("key")
    cs.add_argument("value")

    li = sub.add_parser("icloud-login", help="one-time iCloud 2FA")
    li.add_argument("--code")
    li.add_argument("--code-file")
    li.add_argument("--code-stdin", action="store_true")

    sub.add_parser("vox-prompt", help="print the phone prompt with your list names")

    sub.add_parser("status", help="glance at the spoke's health")

    tp = sub.add_parser("tail", help="follow the mailbox files")
    tp.add_argument("--box", choices=("both", "manager", "vox"), default="both")
    tp.add_argument("-f", "--follow", action="store_true")

    sp = sub.add_parser("send", help="push one reply through the outbound path")
    sp.add_argument("text")
    sp.add_argument("--no-notify", action="store_true")

    dl = sub.add_parser("deliver", help="publish a file as a gist + tappable banner")
    dl.add_argument("file")
    dl.add_argument("--summary")
    dl.add_argument("--public", action="store_true")
    dl.add_argument("--yes", action="store_true", help="skip the confirmation")

    rs = sub.add_parser("radicale-server", help="manage the self-hosted Radicale server")
    rssub = rs.add_subparsers(dest="op")
    rsi = rssub.add_parser("init")
    rsi.add_argument("--user")
    rsi.add_argument("--password")
    rsi.add_argument("--host")
    rsi.add_argument("--port", type=int)
    rst = rssub.add_parser("start")
    rst.add_argument("--background", action="store_true")
    rssub.add_parser("stop")
    rssub.add_parser("status")
    rssub.add_parser("url")
    return p


def _overrides(args) -> dict:
    return parse_overrides(getattr(args, "overrides", None))


def _force_utf8_output() -> None:
    """Emit UTF-8 whatever the console's default codec is.

    On Windows a redirected stdout defaults to cp1252, which cannot encode the
    characters this tool routinely prints — the phone prompt contains `→`, and any
    dictation may contain an emoji. Without this, `voice-bridge vox-prompt | clip`
    dies with a UnicodeEncodeError, and because that subclasses ValueError it was
    being reported as a generic `error:` with exit 2 rather than as the encoding
    problem it is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # absent when the stream is captured (tests)
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # pragma: no cover - exotic stream
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = _build_parser()
    args = parser.parse_args(argv)
    log_mod.configure(verbose=args.verbose, quiet=args.quiet, logfile=args.log_file)
    if not args.cmd:
        parser.print_help()
        return 2

    try:
        return _dispatch(args)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2
    except ValueError as exc:  # e.g. bad --set key
        print(f"error: {exc}")
        return 2


def _dispatch(args) -> int:  # noqa: C901 - a flat command table
    cmd = args.cmd
    cfg_path = args.config

    if cmd == "run":
        return run_command(
            mailbox=args.mailbox,
            transport=args.transport,
            require_mailbox=args.require_mailbox,
            interval=args.interval,
            once=args.once,
            dry_run=args.dry_run,
            config_path=cfg_path,
            overrides=_overrides(args),
            with_server=args.with_server,
        )

    if cmd == "radicale-server":
        return _radicale_server_cmd(args, cfg_path)

    if cmd == "setup":
        return setup_mod.run_setup(
            config_path=cfg_path, transport=args.transport, overrides=_overrides(args)
        )

    if cmd == "config":
        return _config_cmd(args, cfg_path)

    if cmd == "vox-prompt":
        cfg = load_config(cfg_path)
        try:
            text = prompt_mod.render_vox_prompt(cfg)
        except CommandError as exc:
            print(f"error: {exc.msg}", file=sys.stderr)
            return exc.code
        # The prompt itself goes to stdout ALONE, so `vox-prompt | pbcopy` pastes
        # something usable; the human-facing hint goes to stderr.
        print(text)
        print("\n(paste the text above into the Claude app on your phone)", file=sys.stderr)
        return 0

    # commands that need a transport
    cfg = load_config(cfg_path)

    if cmd == "doctor":
        return doctor_mod.run(cfg, fix=args.fix)

    if cmd == "notify":
        if ntfy.push(cfg, args.text, click=args.click):
            print("banner sent.")
            return 0
        print(f"error: no ntfy topic at {cfg.ntfy_topic_file}")
        return 2

    if cmd == "status":
        data = status_mod.gather(cfg)
        if args.json:
            print(json.dumps(data))
        else:
            for k, v in data.items():
                print(f"  {k:<16} : {v}")
        return 0

    if cmd == "tail":
        return _tail_cmd(cfg, args)

    if cmd == "send":
        t = make_transport(cfg)
        try:
            poller_mod.send_reply(cfg, t, args.text, notify=not args.no_notify)
        except Exception as exc:
            print(f"error: {exc}")
            return 2
        print("sent.")
        return 0

    if cmd == "lists":
        t = make_transport(cfg)
        t.connect()
        configured = {cfg.inbox_list, cfg.output_list}
        rows = [
            {"name": r.name, "id": r.id, "configured": r.name in configured}
            for r in t.list_todo_lists()
        ]
        if args.json:
            print(json.dumps(rows))
        else:
            for d in rows:
                mark = " *" if d["configured"] else ""
                print(f"  {d['name']}{mark}\n      id: {d['id']}")
        return 0

    if cmd == "peek":
        t = make_transport(cfg)
        t.connect()
        name = cfg.inbox_list if args.box == "inbox" else cfg.output_list
        lid = cfg.inbox_list_id if args.box == "inbox" else cfg.output_list_id
        lst = t.resolve_list(name, lid)
        items = t.read_completed(lst) if args.completed else t.read_incomplete(lst)
        items = items[: args.limit] if args.limit else items
        if args.json:
            print(json.dumps([{"title": it.title, "notes": it.notes} for it in items]))
        else:
            for it in items:
                print(f"  - {it.title}" + (f" — {it.notes}" if it.notes else ""))
        return 0

    if cmd == "deliver":
        return deliver_mod.deliver(
            cfg, args.file, summary=args.summary, public=args.public, assume_yes=args.yes
        )

    if cmd == "icloud-login":
        return login_mod.icloud_login(
            cfg, code=args.code, code_file=args.code_file, code_stdin=args.code_stdin
        )

    return 2


def _tail_cmd(cfg, args) -> int:
    import time

    boxes = []
    if args.box in ("both", "manager"):
        boxes.append(("manager", cfg.peer_inbox))
    if args.box in ("both", "vox"):
        boxes.append(("vox", cfg.our_inbox))
    sizes: dict = {}
    for label, path in boxes:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                print(f"[{label}] {line}")
            sizes[path] = path.stat().st_size
        else:
            sizes[path] = 0
    if not args.follow:
        return 0
    try:
        while True:
            for label, path in boxes:
                if not path.exists():
                    continue
                data = path.read_bytes()
                if len(data) > sizes[path]:
                    for line in data[sizes[path] :].decode("utf-8", "replace").splitlines():
                        print(f"[{label}] {line}")
                    sizes[path] = len(data)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _radicale_server_cmd(args, cfg_path) -> int:
    cfg = load_config(cfg_path)
    op = getattr(args, "op", None)
    if op == "init":
        server_mod.init(cfg, user=args.user, password=args.password, host=args.host, port=args.port)
        print(f"initialised under {server_mod.paths(cfg).base}")
        return 0
    if op == "start":
        return server_mod.start(cfg, background=args.background)
    if op == "stop":
        return server_mod.stop(cfg)
    if op == "status":
        s = server_mod.status(cfg)
        for k, v in s.items():
            print(f"  {k:<12} : {v}")
        return 0 if s["reachable"] else 1
    if op == "url":
        print(server_mod.client_url(cfg))
        return 0
    print("usage: voice-bridge radicale-server {init|start|stop|status|url}")
    return 2


def _config_cmd(args, cfg_path) -> int:
    op = getattr(args, "op", None)
    if op == "fields":
        for key, default in field_help():
            print(f"  {key:<22} {default}")
        return 0
    if op == "set":
        # Same resolver reads use, or `set` writes a file `get` never looks at.
        path, _ = resolve_config_path(cfg_path, must_exist=False)
        assert path is not None  # must_exist=False always names a target
        set_value(path, args.key, args.value)
        print(f"set {args.key} = {args.value}")
        return 0
    if op == "get":
        val = load_config(cfg_path).as_dict().get(args.key)
        if val is None:
            print(f"error: unknown field {args.key!r}")
            return 2
        print(val)
        return 0
    # default / show
    data = load_config(cfg_path).as_dict()
    if getattr(args, "json", False):
        print(json.dumps(data))
    else:
        for k, v in data.items():
            print(f"  {k:<22} : {v}")
    return 0
