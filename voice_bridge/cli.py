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
from . import tailer
from .config import (
    ConfigError,
    field_help,
    load_config,
    parse_overrides,
    resolve_config_path,
    set_value,
)
from .commands import connected_transport, list_command, peek_command
from .errors import CommandError
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

    li = sub.add_parser("icloud-login", help="set up iCloud credentials + a trusted session")
    li.add_argument("--apple-id", metavar="EMAIL", help="skip the Apple ID prompt")
    # NO --password flag, ever: argv is visible to every other user on the machine.
    li.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin, not a prompt"
    )
    li.add_argument("--new", action="store_true", help="capture fresh credentials, overwriting")
    li.add_argument("--enter-2fa", action="store_true", help="force a fresh 2FA, keeping creds")
    li.add_argument("--code")
    li.add_argument("--code-file")
    li.add_argument("--code-stdin", action="store_true")

    sub.add_parser("vox-prompt", help="print the phone prompt with your list names")

    sub.add_parser("status", help="glance at the spoke's health")

    tp = sub.add_parser("tail", help="follow the mailbox files")
    tp.add_argument("--box", choices=("both", "manager", "vox"), default="both")
    tp.add_argument("-n", type=int, dest="limit", help="last N lines (default 20)")
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
    # NO --password flag: argv is readable by every other user on the machine.
    # The password comes from $RADICALE_PASSWORD or an interactive prompt, the
    # same rule icloud-login follows.
    rsi.add_argument("--host")
    rsi.add_argument("--port", type=int)
    rsi.add_argument("--force", action="store_true", help="rotate existing credentials")
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
    except CommandError as exc:
        # A failure we understand: report it plainly with its own exit code.
        print(f"error: {exc.msg}")
        return exc.code
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
        result = ntfy.push(cfg, args.text, click=args.click)
        if result.status == "sent":
            print("banner sent.")
            return 0
        # 2 = you must act (nothing is configured); 1 = it was configured and the
        # send failed, which is usually transient and worth retrying.
        print(f"error: {result.detail}")
        return 2 if result.status == "no_topic" else 1

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
        # Typed like the others: a missing list is a usage error (2), an
        # unreachable backend is transient (1), and a bug still raises.
        t = connected_transport(cfg)
        poller_mod.send_reply(cfg, t, args.text, notify=not args.no_notify)
        print("sent.")
        return 0

    if cmd == "lists":
        return list_command(cfg, connected_transport(cfg), as_json=args.json)

    if cmd == "peek":
        return peek_command(
            cfg,
            connected_transport(cfg),
            box=args.box,
            completed=args.completed,
            limit=args.limit,
            as_json=args.json,
        )

    if cmd == "deliver":
        return deliver_mod.deliver(
            cfg, args.file, summary=args.summary, public=args.public, assume_yes=args.yes
        )

    if cmd == "icloud-login":
        return login_mod.icloud_login(
            cfg,
            code=args.code,
            code_file=args.code_file,
            code_stdin=args.code_stdin,
            apple_id=args.apple_id,
            password_stdin=args.password_stdin,
            new=args.new,
            enter_2fa=args.enter_2fa,
        )

    return 2


def _tail_cmd(cfg, args) -> int:
    """Show the tail of the mailbox files, optionally following them.

    Both behaviours come from `tailer`, which is where they are actually tested:
    the previous inline loop dumped a file's entire history, and it compared
    `len(data) > size` so a truncated or rotated file left the offset past the
    end — following then went silent for ever, which looks exactly like "nothing
    is happening" (TAIL-1/TAIL-2).
    """
    import time

    boxes = []
    if args.box in ("both", "manager"):
        boxes.append(("manager", cfg.peer_inbox))
    if args.box in ("both", "vox"):
        boxes.append(("vox", cfg.our_inbox))

    limit = tailer.DEFAULT_TAIL_LINES if args.limit is None else args.limit
    offsets: dict = {}
    for label, path in boxes:
        for line in tailer.read_tail(path, limit):
            print(f"[{label}] {line}")
        offsets[path] = path.stat().st_size if path.exists() else 0

    if not args.follow:
        return 0
    try:
        while True:
            for label, path in boxes:
                lines, offsets[path] = tailer.follow_step(path, offsets[path])
                for line in lines:
                    print(f"[{label}] {line}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _radicale_server_cmd(args, cfg_path) -> int:
    cfg = load_config(cfg_path)
    op = getattr(args, "op", None)
    if op == "init":
        try:
            server_mod.init(
                cfg, user=args.user, host=args.host, port=args.port, force=args.force
            )
        except server_mod.ServerExtraMissing as exc:
            print(f"error: {exc}")
            return 2
        except FileExistsError as exc:
            print(f"error: {exc}")
            return 2
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
