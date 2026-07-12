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

> **Status:** the package (`voice_bridge/`) is being built test-first against the design in
> [`docs/.design/voice-bridge.md`](docs/.design/voice-bridge.md). This README describes the
> target CLI; the legacy scripts are being migrated into it.

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

- **Vox-Message-Inbox** — you dictate here (phone → mailbox)
- **Vox-Message-Outbox** — replies surface here (mailbox → phone)

## Everyday commands

    $VB doctor              # survey the whole setup; add --fix to repair
    $VB lists               # every Reminders list with its GUID (pick the exact one)
    $VB peek --box inbox    # what's waiting in a box right now
    $VB notify "done ✅" --click https://github.com/you/repo/pull/42
    $VB config show         # resolved settings   ($VB config --help = every field)
    $VB config set poll_interval 30

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
  `--code-stdin`). Session caches in `pyicloud-cookies/` for ~60 days.
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

Architecture and the build plan: [`docs/.design/voice-bridge.md`](docs/.design/voice-bridge.md).
Superseded standalone-model docs live in [`.archive/`](.archive/).

[agent-to-agent-communication-file-mailbox]: https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox
