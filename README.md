# voice-bridge

Talk to a **Claude Code session on your PC from your phone** — by voice or text —
using **Apple Reminders (or any CalDAV list) as a shared message bus**. The Claude
iPhone app drops a task into a list; a small poller on the PC picks it up, hands it to
your local Claude Code "manager" session, and the manager's replies come back to a
second list on your phone. Long content (designs, docs) is delivered as a tappable gist
link plus a spoken walkthrough.

One machine, **one project**, and your choice of **two buses**: self-hosted **Radicale**
(recommended) or **iCloud** Reminders (fallback).

## Quick start — paste this into a Claude Code session in your project folder

```
Set up a voice bridge for THIS project (the current folder): read
https://github.com/YoraiLevi/ai-voice-reminders-bridge/blob/HEAD/SETUP.md and follow it.
Before creating any config, pause and ASK me (show the options + defaults, then wait):
(1) transport — radicale (self-hosted CalDAV, recommended) or icloud (Apple Reminders, fallback);
(2) names — AUTO (derive "To <Folder>" / "From <Folder>" from this folder's name) or custom
    (I'll give the project name + both list names).
Then provision per SETUP.md with my answers (if custom, write ./.claude/voice-bridge.json from
the matching examples/ sample first), start the poller in the background, and listen.
```

That one paste does the whole setup: it asks your two choices, provisions the config (and, on
Radicale, the two lists), starts the poller, and begins listening. Everything else stays on
sensible [defaults](#config-schema).

> **This channel is ASYNCHRONOUS — not a live call.** It is delayed, turn-based
> message-passing: a reply can reach the phone a full turn (or more) later, every message
> is timestamped `[HH:MM]`, and the newest message on a topic supersedes older ones. The
> full contract — mailbox format, async rules, the one reply path, reliability invariants,
> and honest limits — is in **[`PROTOCOL.md`](PROTOCOL.md)**. Read it before building on top.

## How it works

```
 iPhone (Claude app / Reminders)          PC (Claude Code manager session)
 ┌───────────────────────────┐            ┌────────────────────────────────────┐
 │  inbox list  "To Claude"  │ ─ phone →  │  poller                             │
 │                           │            │    └─ appends to  to-manager.md ────┼─▶ manager reads
 │  output list "From Claude"│ ◀─ PC ───  │  drains  to-phone.md → output list ◀┼── manager appends reply
 └───────────────────────────┘            └────────────────────────────────────┘
        (timestamped, async — a reply may land a turn later)
```

Both buses share the same **file mailbox** (`~/.claude/message-protocol/` by default):
inbound reminders become lines in `to-manager.md`; the manager replies by appending to
`to-phone.md`, which the poller drains into the output list.

- **Radicale (recommended) — `reminder_bridge.py` + `radicale/`:** a tiny self-hosted
  CalDAV server both phone and PC see. A CalDAV client may create lists, so setup is
  self-provisioning. See `radicale/OWNER-SETUP.md`.
- **iCloud (fallback) — `pyicloud_bridge.py`:** Apple's CloudKit Reminders store via the
  pyicloud private web API. Needs a one-time 2FA login (`pyicloud_login.py`) cached ~60
  days, and the two lists must be created by hand on the phone. `probe.py` checks whether
  a given account is reachable over CalDAV before you rely on it.

## Quickstart

```bash
# 1. From the project folder you want to control by voice, provision it.
#    Radicale (recommended — creates the two lists for you):
uv run /path/to/voice-bridge/bootstrap.py --transport radicale
#    ...or iCloud (writes config; make the two lists by hand on the phone):
uv run /path/to/voice-bridge/bootstrap.py

# 2. Start the poller matching your transport (leave it running):
uv run /path/to/voice-bridge/reminder_bridge.py  --config ./.claude/voice-bridge.json  # Radicale
uv run /path/to/voice-bridge/pyicloud_bridge.py  --config ./.claude/voice-bridge.json  # iCloud
```

Or just point a fresh Claude Code session at [`SETUP.md`](SETUP.md) (`claude @SETUP.md`)
and it does STEP-by-STEP provisioning + start for you.

Inspect / test without touching the network:

```bash
uv run pyicloud_bridge.py --show-config    # print the fully resolved settings
uv run pyicloud_bridge.py --dry-run        # config + the exact to-manager.md line, no network
uv run pyicloud_bridge.py --once           # single poll; exit 1=nothing new, 0=new items
uv run pyicloud_bridge.py --selftest       # LIVE end-to-end proof into a temp mailbox
uv run pyicloud_bridge.py --reply "text"   # write one output-list reminder (timestamped)
```

`uv run` reads each script's PEP-723 inline deps and installs them into an ephemeral env
— no venv to manage.

## Config schema

The project's `.claude/voice-bridge.json` is resolved in this order (first that exists):

1. `--config PATH` on any script
2. `$VOICE_BRIDGE_CONFIG`
3. `./.claude/voice-bridge.json` (relative to the current working directory)
4. none found → built-in defaults

Every field is optional and falls back to the default below, so a minimal config (even
`{}`) works. `~` is expanded in every path. **No secrets go in this file** — only the
*locations* of the credential/cookie/topic files under `~/.auth`.

| field | default | meaning |
|-------|---------|---------|
| `name` | `"voice-bridge"` | project label; also namespaces the seen-files |
| `inbox_list` | `"To Claude"` | list the phone writes to (phone → PC) |
| `output_list` | `"From Claude"` | list replies go to (PC → phone) |
| `inbox_list_id` / `output_list_id` | `""` | optional: pin a list by CloudKit record id (ghost-list fix) |
| `from_name` | `"owner-phone"` | tag in each mailbox line `- [HH:MM] (from_name) …` |
| `mailbox_dir` | `"~/.claude/message-protocol"` | the manager's file mailbox |
| `to_manager` | `"to-manager.md"` | inbound file (relative to `mailbox_dir`) |
| `to_phone` | `"to-phone.md"` | reply file the loop drains (relative to `mailbox_dir`) |
| `ntfy_topic_file` | `"~/.auth/ntfy-topic.txt"` | file holding the ntfy topic |
| `creds_env` | `"~/.auth/icloud.env"` | `KEY=value` creds file (`radicale.env` for Radicale) |
| `cookie_dir` | `"~/.auth/pyicloud-cookies"` | pyicloud trusted-session cache |
| `poll_interval` | `10` | loop cadence (seconds) |

Ready samples: [`examples/voice-bridge.radicale.json`](examples/voice-bridge.radicale.json)
and [`examples/voice-bridge.icloud.json`](examples/voice-bridge.icloud.json).

## Credentials — `~/.auth` (never in the repo)

Secrets are read only from files under `~/.auth`; nothing secret is committed or printed
(only a masked id is logged).

- **Radicale:** `~/.auth/radicale.env` — `ICLOUD_CALDAV_URL` (your Radicale URL) + the
  Radicale user/password. Full owner walkthrough: `radicale/OWNER-SETUP.md`.
- **iCloud:** `~/.auth/icloud.env` — `ICLOUD_APPLE_ID` + `ICLOUD_PASSWORD` (the **main**
  Apple ID password). First login needs a 6-digit 2FA code; the trusted session caches
  under `~/.auth/pyicloud-cookies/` for ~60 days. Full walkthrough: `docs/OWNER-SETUP.md`.
- **ntfy:** `~/.auth/ntfy-topic.txt` — one line, your private ntfy.sh topic (phone banners).

## The ntfy + gist content-delivery pattern

`deliver_content.sh <markdown-file> "one-line summary"` does three things at once:
publishes the Markdown as an unlisted **GitHub gist** (the readable artifact), pushes a
**tappable ntfy notification** whose tap opens the gist, and drops the **full text + link
into the output list** so the phone assistant can read a short spoken walkthrough. `gh`
must be authed; the ntfy topic comes from the config's `ntfy_topic_file`.

## Files

| path | role |
|------|------|
| `PROTOCOL.md` | **the contract** — mailbox, async rules, reply path, invariants, limits |
| `SETUP.md` | agent-facing setup prompt (both transports; `claude @SETUP.md`) |
| `config.py` | config resolution + `~` expansion + `python config.py [PATH]` to print settings |
| `bootstrap.py` | one provisioner; `--transport icloud` (default) or `--transport radicale` |
| `pyicloud_bridge.py` | iCloud transport (CloudKit private API): poll, reply, drain, `--dry-run`/`--selftest` |
| `pyicloud_login.py` | one-time 2FA login → cached trusted session |
| `reminder_bridge.py` | Radicale/CalDAV transport — same mailbox contract |
| `_caldav.py` | shared CalDAV plumbing (creds, connect, list discovery) |
| `probe.py` | read-only GO/NO-GO CalDAV feasibility probe |
| `deliver_content.sh` | gist + ntfy + voice content delivery |
| `radicale/` | self-hosted CalDAV server (config, launcher, user + list bootstrap) + OWNER-SETUP |
| `docs/BRIDGE-INSTRUCTIONS.md` | phone-side + PC-side prompt templates |
| `examples/` | one ready config per transport |
| `.archive/` | superseded material (the former multi-project layer, deep-dive docs) |

## Status

Working single-project version. Known rough edges: no packaged installer / Task-Scheduler
auto-start yet; the pyicloud private API can change under Apple (the Radicale bus is the
documented fallback if it does). The former multi-project machinery lives in `.archive/`.
