"""The `voice-bridge` command - argparse subcommands over the package. Uniform exit
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
from . import progress
from . import prompt as prompt_mod
from . import server as server_mod
from . import setup as setup_mod
from . import status as status_mod
from . import tailer
from .config import (
    SETTABLE_KEYS,
    ConfigError,
    default_config_path,
    field_help,
    load_config,
    parse_overrides,
    resolve_config_path,
    set_value,
)
from .commands import connected_transport, list_command, peek_command, select_command
from .errors import CommandError
from .invocation import path_note
from .prompting import Cancelled
from .runner import run_command

#: Imported, not restated. This was a third copy of the closed set - `factory`,
#: setup's preamble, and here - and the flag audit found it while looking for
#: something else. Three copies of one fact is two chances to disagree.
from .factory import TRANSPORTS as _TRANSPORTS  # noqa: E402 - grouped with its reason

#: The commands whose OUTPUT changes shape for `--json`. A global flag that silently
#: does nothing on most commands is a promise to a script that the script cannot
#: check: it asked for JSON, got prose, and had no way to know.
#:
#: `config show` was MISSING from the first version of this set, so the note that
#: exists to stop `--json` lying started lying itself - that path has always worked,
#: inside `_config_cmd` rather than in the dispatch table I read to build the list.
#:
#: Entries are "cmd" or "cmd op", because the truth is finer than command level:
#: `config show --json` emits JSON and `config fields --json` does not. Declaring
#: `config` wholesale would have been a second, smaller version of the same lie.
#: Two tests check the set in both directions - a hand-maintained list of what the
#: code does is the fact-in-two-places defect the batch it shipped in was about.
_JSON_COMMANDS = frozenset({"setup", "status", "lists", "peek", "config show"})


def _json_target(args) -> str:
    """What the user asked to have formatted: `cmd`, or `cmd op` where there is one."""
    op = getattr(args, "op", None)
    return f"{args.cmd} {op}" if op else str(args.cmd)


#: One convention across every command, so a script can branch on the code
#: without special-casing: 0 did the thing · 1 nothing to do, or a transient
#: failure worth retrying · 2 you must act.
_EXIT_CODES = """
exit codes:
  0  success
  1  nothing to do, or a transient failure worth retrying
  2  you must act - usage, configuration, or something missing
"""

_RAW = argparse.RawDescriptionHelpFormatter


def _epilog(extra: str = "") -> str:
    """A command's own caveats, followed by the shared exit-code table."""
    return (extra.rstrip() + "\n" if extra else "") + _EXIT_CODES


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voice-bridge",
        description="A voice spoke for a file-mailbox.",
        epilog=_EXIT_CODES,
        formatter_class=_RAW,
    )
    p.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json")
    p.add_argument("-v", "--verbose", action="store_true", help="INFO logging")
    p.add_argument("-q", "--quiet", action="store_true", help="errors only")
    p.add_argument("--log-file", metavar="PATH", help="also log to this file")
    p.add_argument(
        "--json",
        action="store_true",
        # "where supported" was a true statement that told you nothing: the reader
        # still cannot find out where without reading the source, and a script that
        # guesses wrong parses prose as JSON. Name them.
        help=f"machine-readable output ({', '.join(sorted(_JSON_COMMANDS))} only)",
    )
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser(
        "run",
        help="ensure everything, then bridge (the main command)",
        formatter_class=_RAW,
        epilog=_epilog(
            "--once exits 1 when it ran fine and there was simply nothing new - that is a\n"
            "normal result, not a failure. It refuses to start beside a live poller unless\n"
            "--force is given, and --dry-run writes nothing at all."
        ),
    )
    r.add_argument("--mailbox", metavar="DIR", help="override the configured mailbox directory")
    r.add_argument("--transport", choices=_TRANSPORTS, help="use this backend for this run only")
    r.add_argument(
        "--require-mailbox",
        action="store_true",
        help="refuse to CREATE a missing mailbox - fail instead",
    )
    r.add_argument("--interval", type=int, metavar="SECONDS", help="override the poll interval")
    r.add_argument("--once", action="store_true", help="run a single poll cycle, then exit")
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would happen and write NOTHING - not even a config file",
    )
    r.add_argument(
        "--force", action="store_true", help="run even if another poller holds the mailbox"
    )
    r.add_argument(
        "--with-server", action="store_true", help="(radicale) spawn the server as a child"
    )
    r.add_argument(
        "--set",
        action="append",
        dest="overrides",
        metavar="KEY=VALUE",
        # The audit's sharpest find: the same flag name PERSISTS on `setup` and does
        # not here. Saying so is the fix; making them agree would be worse, because
        # both behaviours are the right one for their command.
        help="override a config field for THIS RUN only, without writing it (repeatable)",
    )

    s = sub.add_parser(
        "setup",
        help="provision the transport (config + lists)",
        formatter_class=_RAW,
        epilog=_epilog(
            "Prompts only on a terminal; --set pre-answers a field and skips its prompt.\n"
            "--verify runs the WHOLE guided setup and proves the round trip as part of it.\n"
            "If all you want is the check, use `voice-bridge verify` - one probe, no questions."
        ),
    )
    s.add_argument(
        "--transport",
        choices=_TRANSPORTS,
        # NO DEFAULT, deliberately. `default="icloud"` meant a flag nobody typed
        # arrived at `write_config` as an explicit answer and stamped iCloud over a
        # radicale config on disk - converting a working install and, after batch 11
        # correctly recognised that as a transport switch, clearing both list pins
        # with it. `run --transport` never had a default and was never affected.
        #
        # Section 26 says a request is not a decision. A flag's DEFAULT is not even a
        # request: nobody asked for it.
        help="the backend to configure (default: keep what the config says)",
    )
    s.add_argument(
        "--set",
        action="append",
        dest="overrides",
        metavar="KEY=VALUE",
        help="set a config field PERMANENTLY and skip its prompt (repeatable)",
    )
    s.add_argument(
        "--verify",
        action="store_true",
        # The old help said "prove a message round-trips", which is what the flag
        # DOES and not what the command does. Asked from a phone: "What is the
        # purpose of setup --verify? It seems unclear and confusing." They had
        # answered five guided steps to reach one probe. The flag was not lying about
        # itself; it was silent about the ceremony around it.
        help="run the full guided setup AND prove the round trip (see `verify` for the check alone)",
    )

    v = sub.add_parser(
        "verify",
        help="prove a message makes the round trip, and nothing else",
        formatter_class=_RAW,
        epilog=_epilog(
            "One probe each way, then it cleans up after itself. No prompts, no config\n"
            "written, no steps: exit 0 means a message made it, exit 2 means it did not.\n"
            "Not a `doctor` check, because this WRITES - doctor inspects, this sends."
        ),
    )
    del v  # no flags of its own; the verb is the whole interface

    d = sub.add_parser(
        "doctor",
        help="survey the setup; --fix repairs safe items",
        formatter_class=_RAW,
        epilog=_epilog(
            "Read-only: the survey never creates anything, so a missing mailbox is REPORTED\n"
            "rather than silently made - --fix is the consent to repair. The exit code is the\n"
            "worst row found, so a script can gate on it."
        ),
    )
    d.add_argument(
        "--fix",
        action="store_true",
        help="repair the safe items (a stale selection is CLEARED, never re-resolved)",
    )

    ls = sub.add_parser(
        "lists",
        help="enumerate transport lists with their ids",
        formatter_class=_RAW,
        epilog=_epilog(
            "--select chooses which list each role uses. It opens with the roles and\n"
            "their current selections, so you pick what to change and leave - it does\n"
            "not walk you through every list. It runs even when a name is unambiguous,\n"
            "because 'change' means change. It needs a terminal.\n"
            "\n"
            "The rest of the toolkit, so the verbs are discoverable together:\n"
            "  voice-bridge config set inbox_list_id <id>  set the id directly\n"
            "  voice-bridge doctor                         report a selection gone stale\n"
        ),
    )
    ls.add_argument(
        "--select",
        action="store_true",
        help="choose which list each role uses (needs a terminal)",
    )

    rs = sub.add_parser(
        "reset",
        help="delete settings so you can start over",
        formatter_class=_RAW,
        epilog=_epilog(
            "Puts this machine back to just installed, never configured, so setup and\n"
            "login can be run again from zero. Credentials go too: that is the point of\n"
            "the command, not an extra tier. Your mailbox, its messages, and any\n"
            "self-hosted lists are NEVER removed by reset.\n"
            "\n"
            "Every path is listed before anything is asked, and confirmation is typing\n"
            "RESET in full rather than y. Use `uninstall` to remove everything."
        ),
    )
    rs.add_argument("--yes", action="store_true", help="skip the confirmation (scripts, non-TTY)")

    un = sub.add_parser(
        "uninstall",
        help="remove everything this put on this machine",
        formatter_class=_RAW,
        epilog=_epilog(
            "Puts this machine back to as if never installed. Complete by default:\n"
            "config, credentials, session, topic, dedupe state, self-hosted server files\n"
            "and their lists, AND your mailbox with its messages. --keep-mailbox is the\n"
            "one opt-out; for anything more partial you wanted `reset`.\n"
            "\n"
            "Nothing on your Apple or GitHub account is touched: reminder lists and\n"
            "published gists are not on this machine and are not this command's to\n"
            "remove.\n"
            "\n"
            "Confirmation is typing UNINSTALL in full."
        ),
    )
    un.add_argument("--keep-mailbox", action="store_true", help="keep the mailbox and its messages")
    un.add_argument("--yes", action="store_true", help="skip the confirmation (scripts, non-TTY)")

    pk = sub.add_parser("peek", help="show messages in a box")
    pk.add_argument(
        "--box",
        choices=("inbox", "outbox"),
        default="inbox",
        help="which reminder LIST to read: inbox = your dictations, outbox = replies to you",
    )
    pk.add_argument("--completed", action="store_true", help="show items already marked done")
    pk.add_argument("-n", type=int, dest="limit", metavar="N", help="show at most N items")

    n = sub.add_parser(
        "notify",
        help="push a phone banner",
        formatter_class=_RAW,
        epilog=_epilog(
            "Exits 2 when no topic is configured and 1 when a configured send failed -\n"
            "the two used to be reported identically, so a network outage looked like\n"
            "missing configuration."
        ),
    )
    n.add_argument("text")
    n.add_argument(
        "--click", metavar="URL", help="open this URL when the banner is tapped on the phone"
    )

    c = sub.add_parser(
        "config",
        help="show/get/set settings",
        formatter_class=_RAW,
        epilog=_epilog(
            "Which list each role uses is NOT set here - it is chosen by id with\n"
            "`voice-bridge lists --select`, and list names are read from your account.\n"
            "`config show` prints those under 'derived', apart from the settable keys."
        ),
    )
    csub = c.add_subparsers(dest="op")
    # Every subcommand carries its own help. Three of these four printed bare in
    # `config --help`, so the listing told you the verbs existed and nothing else.
    csub.add_parser("show", help="print the fully-resolved settings, including derived ones")
    csub.add_parser("fields", help="list every settable field and its default")
    cg = csub.add_parser("get", help="print one setting's resolved value")
    cg.add_argument("key")
    cs = csub.add_parser("set", help="write one setting to the config file")
    cs.add_argument("key")
    cs.add_argument("value")

    li = sub.add_parser(
        "icloud-login",
        help="set up iCloud credentials + a trusted session",
        formatter_class=_RAW,
        epilog=_epilog(
            "There is deliberately no --password flag: argv is readable by every other user\n"
            "on the machine. Use --password-stdin, or the hidden prompt. This needs the MAIN\n"
            "Apple ID password, not an app-specific one."
        ),
    )
    li.add_argument("--apple-id", metavar="EMAIL", help="skip the Apple ID prompt")
    # NO --password flag, ever: argv is visible to every other user on the machine.
    li.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin, not a prompt"
    )
    li.add_argument("--new", action="store_true", help="capture fresh credentials, overwriting")
    li.add_argument("--enter-2fa", action="store_true", help="force a fresh 2FA, keeping creds")
    li.add_argument("--code", metavar="DIGITS", help="the 2FA code, if you already have it")
    li.add_argument("--code-file", metavar="PATH", help="read the 2FA code from this file")
    li.add_argument("--code-stdin", action="store_true", help="read the 2FA code from stdin")

    sub.add_parser("vox-prompt", help="print the phone prompt with your list names")
    sub.add_parser(
        "peer-prompt",
        help="print the prompt that makes a coding agent your peer",
        formatter_class=_RAW,
        epilog=_epilog(
            "Paste it at an agent on this machine. It names your two mailbox files and\n"
            "the line format, so the agent can join without knowing this tool exists.\n"
            "`run` writes the same text to PEER-PROMPT.md when it creates a mailbox."
        ),
    )

    sub.add_parser("status", help="glance at the spoke's health")

    tp = sub.add_parser(
        "tail",
        help="follow the mailbox files",
        formatter_class=_RAW,
        epilog=_epilog(
            "Shows the last 20 lines by default (-n to change); --follow survives the file\n"
            "being truncated or rotated instead of going quietly silent."
        ),
    )
    tp.add_argument(
        "--box",
        choices=("both", "manager", "vox"),
        default="both",
        # NOTE: `peek --box` selects a reminder LIST; this selects a mailbox FILE.
        # One flag name, two subjects - recorded in the batch-11 flag audit.
        help="which mailbox FILE to follow (not a reminder list - see `peek --box`)",
    )
    tp.add_argument("-n", type=int, dest="limit", help="last N lines (default 20)")
    tp.add_argument(
        "-f", "--follow", action="store_true", help="keep watching and print new lines as they land"
    )

    sp = sub.add_parser("send", help="push one reply through the outbound path")
    sp.add_argument("text")
    sp.add_argument(
        "--no-notify",
        action="store_true",
        help="write the reply to the list without buzzing the phone",
    )

    dl = sub.add_parser("deliver", help="publish a file as a gist + tappable banner")
    dl.add_argument("file")
    dl.add_argument("--summary", metavar="TEXT", help="the gist description")
    dl.add_argument(
        "--public",
        action="store_true",
        # NOT "a secret link". `deliver`'s own confirmation prompt says the opposite and is
        # right: "A private gist is UNLISTED, not secret - anyone with the link can read it."
        # Two artifacts describing one GitHub behaviour, disagreeing, and the reassuring one
        # was the help text - which is the direction that costs somebody something. Found by
        # a doc reviewer comparing the two.
        help="publish the gist PUBLICLY and searchably (default: unlisted, not private)",
    )
    dl.add_argument("--yes", action="store_true", help="skip the confirmation")

    rs = sub.add_parser("radicale-server", help="manage the self-hosted Radicale server")
    rssub = rs.add_subparsers(dest="op")
    rsi = rssub.add_parser("init")
    rsi.add_argument("--user", metavar="NAME", help="the CalDAV account to create")
    # NO --password flag: argv is readable by every other user on the machine.
    # The password comes from $RADICALE_PASSWORD or an interactive prompt, the
    # same rule icloud-login follows.
    rsi.add_argument(
        "--host", metavar="ADDR", help="address to bind (see the warning about 0.0.0.0)"
    )
    rsi.add_argument("--port", type=int, metavar="PORT", help="port to bind")
    rsi.add_argument(
        "--force",
        action="store_true",
        # Understated: it rewrites the server config and the bcrypt user file too,
        # and the phone stops connecting until its CalDAV account is updated. The
        # FileExistsError said so; the flag that causes it did not.
        help="rewrite the server config and ROTATE the password - the phone's CalDAV "
        "account must be updated to match",
    )
    rst = rssub.add_parser("start")
    rst.add_argument("--background", action="store_true", help="run the server as a detached child")
    rssub.add_parser("stop")
    rssub.add_parser("status")
    rssub.add_parser("url")
    return p


def _overrides(args) -> dict:
    return parse_overrides(getattr(args, "overrides", None))


def _force_utf8_output() -> None:
    """Emit UTF-8 whatever the console's default codec is.

    On Windows a redirected stdout defaults to cp1252, which cannot encode the
    characters this tool routinely prints - the phone prompt contains `→`, and any
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
    progress.configure(quiet=args.quiet)
    if not args.cmd:
        parser.print_help()
        return 2

    # Said ONCE, before any output that will name the command, and only when it is
    # actually untypeable here. Every "Next: voice-bridge ..." line below is right
    # for an installed package and wrong in a project checkout, and a user who
    # followed one got "not recognized" from the tool that had just suggested it.
    note = path_note()
    if note:
        print(note, file=sys.stderr)

    # A flag that did nothing says so. Not an error - the command still does exactly
    # what was asked, only the formatting request did not apply - but a script that
    # passes `--json` and receives prose deserves to be told, on stderr, where it
    # cannot corrupt the output it is parsing.
    target = _json_target(args)
    if args.json and target not in _JSON_COMMANDS:
        print(
            f"note: --json has no effect on `{target}` - "
            f"supported by: {', '.join(sorted(_JSON_COMMANDS))}",
            file=sys.stderr,
        )

    try:
        return _dispatch(args)
    except Cancelled:
        # Ctrl-C at a prompt is a decision, not a fault. ONE handler, because the
        # prompts are many and a traceback at the moment someone is being careful
        # suggests the tool broke when it simply stopped.
        print("cancelled - nothing was done.")
        return 1
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
            force=args.force,
        )

    if cmd == "radicale-server":
        return _radicale_server_cmd(args, cfg_path)

    if cmd == "setup":
        return setup_mod.run_setup(
            config_path=cfg_path,
            transport=args.transport,
            overrides=_overrides(args),
            do_verify=args.verify,
            as_json=args.json,
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

    if cmd == "peer-prompt":
        cfg = load_config(cfg_path)
        try:
            text = prompt_mod.render_peer_prompt(cfg)
        except CommandError as exc:
            print(f"error: {exc.msg}", file=sys.stderr)
            return exc.code
        # Same split as vox-prompt: the pasteable text alone on stdout.
        print(text)
        print("\n(paste the text above at a coding agent on this machine)", file=sys.stderr)
        return 0

    # commands that need a transport
    cfg = load_config(cfg_path)

    if cmd == "verify":
        # The factory is PASSED, not called: `verify_command` checks its offline
        # precondition first, so a run with no lists selected never touches the
        # account. Calling `connected_transport(cfg)` here authenticated first and
        # refused second.
        return setup_mod.verify_command(cfg, connected_transport)

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

    if cmd in ("reset", "uninstall"):
        from . import teardown

        path, _ = resolve_config_path(args.config, must_exist=False)
        return teardown.run_teardown(
            cfg,
            path or default_config_path(),
            verb=cmd,
            keep_mailbox=getattr(args, "keep_mailbox", False),
            assume_yes=args.yes,
        )

    if cmd == "lists":
        if args.select:
            path, _ = resolve_config_path(args.config, must_exist=False)
            return select_command(
                cfg,
                connected_transport(cfg),
                config_path=path or default_config_path(),
            )
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
    end - following then went silent for ever, which looks exactly like "nothing
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
            server_mod.init(cfg, user=args.user, host=args.host, port=args.port, force=args.force)
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
        # The line above is the PC-side URL; these are the addresses that reach a
        # non-loopback bind. Listed rather than chosen - which network a device is on is
        # not something this machine can know.
        #
        # And they are labelled as unusable by a phone, because they are: an iPhone
        # refuses plain HTTP for a CalDAV account. That was established in the field
        # AFTER this listing was added to stop `url` printing something unusable - so
        # without the caveat the fix would have reintroduced its own defect in a new
        # form. An iOS account needs TLS, which `tailscale serve` provides.
        candidates = server_mod.phone_urls(cfg)
        for label, url in candidates:
            print(f"  reachable on your {label}:  {url}")
        if candidates:
            print("  NOTE: these are plain HTTP. An iPhone will NOT accept them for a")
            print("        CalDAV account - it requires TLS. Put `tailscale serve` in")
            print("        front and use the https://<node>.ts.net/ URL it prints.")
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
        # Unchanged and unsplit: a machine reading this wants the state, and a
        # grouping that exists to teach a human would just be a schema change.
        print(json.dumps(data))
        return 0

    # SETTABLE and DERIVED are printed apart, because printed together they read
    # as one list of knobs. `from_name` and `spoke_name` sat side by side with
    # identical values and no hint which one did anything - so setting `from_name`
    # looked reasonable, changed nothing visible, and cost a user real time.
    derived = {k: v for k, v in data.items() if k not in SETTABLE_KEYS and k != "source"}
    for k, v in data.items():
        if k in derived:
            continue
        print(f"  {k:<22} : {v}")
    if derived:
        print("")
        print("  derived - read-only, changed by the commands that own them:")
        for k, v in derived.items():
            print(f"    {k:<20} : {v}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    # Without this, `python -m voice_bridge.cli` imports the module, defines
    # main(), never calls it, and exits 0 - a command that succeeds at nothing.
    raise SystemExit(main())
