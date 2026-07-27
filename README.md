# voice-bridge

**Add a voice spoke to a file-mailbox agent system.** Talk to the agents in your mailbox
from your phone, by voice, through Apple Reminders — you dictate a Reminder, `voice-bridge`
delivers it into the mailbox, and replies come back as a Reminder plus a tappable
notification.

It **extends** the file-mailbox protocol
([agent-to-agent-communication-file-mailbox]) — it does *not* set up the mailbox or the
agents; it plugs a phone spoke (`vox`) into one that already exists. It exchanges messages
with a **peer** in the mailbox (a manager, by default; any spoke if you point `route_to`
elsewhere).

> **Async channel.** Replies can arrive a turn or more later; every message is timestamped
> `[HH:MM]` and the newest on a topic supersedes older ones.

> **Status:** built, green, and live-verified — 294 tests across unit / contract /
> integration / end-to-end, CI green on Linux and Windows, and a real voice dictation has
> made the full round trip through a live Apple account. The honest list of what does and
> does not hold today is in [`docs/design.md`](docs/design.md).

## Prerequisite

A running file-mailbox with a peer (e.g. a manager) joined. If you don't have one, set it
up first — one paste, see [agent-to-agent-communication-file-mailbox]. `voice-bridge`
attaches the `vox` spoke to it (and will create the bare mailbox files if you point it at an
empty dir, but never a peer).

## Run it — nothing installed, straight from GitHub

Every example uses `$VB`; define it once:

    VB='uvx --from git+https://github.com/YoraiLevi/ai-voice-reminders-bridge voice-bridge'

One command sets up **and** runs — it bootstraps whatever's missing and asks only what it
can't infer:

    $VB run

No arguments? It prompts for the mailbox (default `~/.agent-mail`), provisions the phone
transport, logs in, looks up/creates the two lists, verifies a round-trip, then bridges.
Re-run any time — it only fills gaps.

Prefer to set up first and run later:

    $VB setup      # provision + verify end-to-end, then stop
    $VB run        # later — detects it's ready and just bridges

On start it announces the `vox` spoke into the mailbox and ejects cleanly on stop, so your
peer sees it join and leave like any worker.

## The phone side

    $VB vox-prompt          # prints the Vox prompt with your list names; paste into the Claude app

## Two Reminders lists (names configurable)

The names read from **your** seat, not the tool's:

- **Vox-Message-Outbox** — *you* dictate here; it is your outgoing
- **Vox-Message-Inbox** — answers arrive here; it is your incoming

## Everyday commands

    $VB doctor              # survey the whole setup; add --fix to repair
    $VB lists               # every Reminders list with its GUID (pick the exact one)
    $VB peek --box inbox    # what's waiting in a box right now
    $VB notify "done ✅" --click https://github.com/you/repo/pull/42
    $VB config show         # resolved settings   ($VB config --help = every field)
    $VB config set poll_interval 30

## Command reference

| command | what it does |
|---|---|
| `run` | ensure everything is set up, then bridge continuously |
| `setup [--verify]` | provision config, auth and lists; `--verify` proves a message round-trips |
| `doctor [--fix]` | survey every interface; read-only unless `--fix` |
| `status` | a glance at the spoke's health |
| `lists` | every backend list with its ID (needed to pin a ghost) |
| `peek --box inbox\|outbox [-n N]` | what is sitting in a box right now |
| `tail [-n N] [-f]` | follow the mailbox files |
| `send TEXT` | push one message through the outbound path |
| `notify TEXT [--click URL]` | send a push notification |
| `deliver FILE` | publish a file and send a tappable link |
| `config show \| get \| set \| fields` | inspect and edit settings |
| `icloud-login` | establish the iCloud session, including two-factor |
| `vox-prompt` | print the phone-side prompt, rendered with your list names |
| `radicale-server init \| start \| stop \| status \| url` | manage the self-hosted backend |

**Exit codes are uniform**, so you can script against them: **0** success · **1** nothing to
do, or a transient failure worth retrying · **2** you must act (usage, configuration, or
something missing).

> **`run --once` exiting 1 is normal.** It means the cycle ran fine and there was simply
> nothing new to move — not that anything failed. Treat 2 as the error case.

Every command's `--help` carries its own caveats and the exit-code table.

## Transports

| | **iCloud** (default) | **Radicale** |
|---|---|---|
| setup | Apple ID + password in the creds file + one-time 2FA | self-hosted CalDAV server + a phone CalDAV account |
| lists | create the two by hand on the phone | created for you |
| speed | fast | slower; needs a VPN/tunnel to reach your server |

## Credentials & state — `~/.local/state/vox-mailbox/` (never in the repo)

Everything private lives under one XDG state dir (`$XDG_STATE_HOME/vox-mailbox`), so there
are no conflicts with other tools:

- **iCloud:** `icloud.env` → `ICLOUD_APPLE_ID` + `ICLOUD_PASSWORD` (main Apple ID password).
  First login: `$VB icloud-login` (accepts the code interactively, or `--code-file`, or
  `--code-stdin`). The session caches in `pyicloud-cookies/` and expires periodically — the
  exact lifetime is not documented by Apple, so watch for a 2FA prompt and re-run the login.
- **Radicale:** `radicale.env` → `ICLOUD_CALDAV_URL` + Radicale user/pass.
- **ntfy:** `ntfy-topic.txt` → your private topic (for banners).

## Tell your peer (one line)

Paste into your manager/agent once: *"the `vox` spoke is async/turn-based — timestamp
replies, and newest supersedes older on a topic."* That's all it needs; `voice-bridge` never
touches it.

## Limits

Delivery is by convention (append + poll), not a confirmed queue — no ack. One phone per
spoke; single-machine mailbox. Apple's private API can change — Radicale is the same-contract
fallback.

## Development

    uv run --extra dev pytest        # the test suite (unit / contract / integration / e2e)
    uv run --extra dev ruff check voice_bridge tests

Architecture, guarantees, and known limits: [`docs/design.md`](docs/design.md).

[agent-to-agent-communication-file-mailbox]: https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox
