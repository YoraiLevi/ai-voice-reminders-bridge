# voice-bridge

Talk to a **Claude Code session on your PC from your phone** — by voice or text —
using **Apple Reminders (or any CalDAV list) as a shared message bus**. The Claude
iPhone app drops a task into a list; a small poller on the PC picks it up, hands it to
your local Claude Code "manager" session, and the manager's replies come back to a
second list on your phone. The voice persona on the phone is **Vox** — the default
lists are `To Vox` / `From Vox`.

One machine, **one project**, and your choice of **two buses**: **iCloud** Reminders
(default) or self-hosted **Radicale**.

- **iCloud** — fast; needs your Apple ID + password in `~/.auth/icloud.env` and a one-time
  2FA code.
- **Radicale** — no Apple password, but slower to sync and needs a VPN/tunnel so the phone
  can reach your server, plus a CalDAV account added on the iPhone.

## Quick start — paste this into a Claude Code session in your project folder

```
Set up a voice bridge for THIS project (the current folder): read
https://github.com/YoraiLevi/ai-voice-reminders-bridge/blob/HEAD/SETUP.md and follow it.
First ensure a local voice-bridge checkout exists — ask me for a path to it, to clone it,
or a single dir to search; do NOT assume one. Then, before creating any config, ASK me, then wait:
(1) transport — icloud (default; Apple Reminders, needs Apple ID+password in a file + 2FA)
    or radicale (self-hosted CalDAV; no Apple password but slower + needs a VPN/tunnel and a
    CalDAV account on the iPhone);
(2) settings — show me EVERY config field with its default (name defaults to "vox" → lists
    "To Vox"/"From Vox", from_name, mailbox_dir, to_manager, to_phone, ntfy_topic_file,
    cookie_dir, poll_interval, and the optional list-id pins) and let me accept all or override any.
Then provision per SETUP.md, passing one --set KEY=VALUE per field I overrode, start the poller
in the background, and listen.
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
 │  inbox list  "To Vox"     │ ─ phone →  │  poller                             │
 │                           │            │    └─ appends to  to-manager.md ────┼─▶ manager reads
 │  output list "From Vox"   │ ◀─ PC ───  │  drains  to-phone.md → output list ◀┼── manager appends reply
 └───────────────────────────┘            └────────────────────────────────────┘
        (timestamped, async — a reply may land a turn later)
```

Both buses share the same **file mailbox** (`~/.claude/message-protocol/` by default):
inbound reminders become lines in `to-manager.md`; the manager replies by appending to
`to-phone.md`, which the poller drains into the output list.

- **iCloud (default) — `pyicloud_bridge.py`:** Apple's CloudKit Reminders store via the
  pyicloud private web API. Needs a one-time 2FA login (`pyicloud_login.py`) cached ~60
  days, and the two lists must be created by hand on the phone. `probe.py` checks whether
  a given account is reachable over CalDAV before you rely on it.
- **Radicale — `reminder_bridge.py` + `radicale/`:** a tiny self-hosted CalDAV server both
  phone and PC see. A CalDAV client may create lists, so setup is self-provisioning (no
  phone-side list step), at the cost of running the server + a phone CalDAV account. See
  `radicale/OWNER-SETUP.md`.

## Quickstart

```bash
# 1. From the project folder you want to control by voice, provision it.
#    iCloud (default — writes config; make the two lists by hand on the phone):
uv run /path/to/voice-bridge/bootstrap.py
#    ...or Radicale (creates the two lists for you):
uv run /path/to/voice-bridge/bootstrap.py --transport radicale

# 2. Start the poller matching your transport (leave it running):
uv run /path/to/voice-bridge/pyicloud_bridge.py  --config ./.claude/voice-bridge.json  # iCloud
uv run /path/to/voice-bridge/reminder_bridge.py  --config ./.claude/voice-bridge.json  # Radicale
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
| `inbox_list` | `"To Vox"` | list the phone writes to (phone → PC) |
| `output_list` | `"From Vox"` | list replies go to (PC → phone) |
| `inbox_list_id` / `output_list_id` | `""` | optional: pin a list by CloudKit record id (ghost-list fix) |
| `from_name` | `"owner-phone"` | tag in each mailbox line `- [HH:MM] (from_name) …` |
| `mailbox_dir` | `"~/.claude/message-protocol"` | the manager's file mailbox |
| `to_manager` | `"to-manager.md"` | inbound file (relative to `mailbox_dir`) |
| `to_phone` | `"to-phone.md"` | reply file the loop drains (relative to `mailbox_dir`) |
| `ntfy_topic_file` | `"~/.auth/ntfy-topic.txt"` | file holding the ntfy topic |
| `ntfy_server` | `"https://ntfy.sh"` | ntfy server banners POST to (override for self-hosted ntfy) |
| `ntfy_title` | `"Vox · {name}"` | banner title; `{name}` is replaced with `name` |
| `ntfy_tags` | `"robot"` | ntfy tags (banner icon), comma-separated |
| `ntfy_priority` | `"high"` | ntfy priority (`max`/`high`/`default`/`low`/`min`) |
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

## Sending a phone notification (ntfy)

Push a banner to your phone. The topic comes from your config's `ntfy_topic_file` — so this
honours whatever you configured, nothing is hardcoded:

```bash
uv run /path/to/voice-bridge/pyicloud_bridge.py --config ./.claude/voice-bridge.json \
    --notify "Build finished ✅"
```

Attach a URL to make the banner **tappable** — tapping opens the link (a PR, a doc, any URL
you already have), so there's nothing to publish yourself:

```bash
uv run …/pyicloud_bridge.py --config … --notify "Design ready — tap to open the PR" \
    --click "https://github.com/you/repo/pull/42"
```

(Use `reminder_bridge.py` instead on the Radicale transport — same flags.) The `--click` URL
is the owner's easy button: hand it any link and the notification becomes a one-tap open.
**If you need to surface long content and have no URL for it, ask the owner first whether to
publish it as a *private* gist** (`gh gist create --private file.md`) purely to obtain a link
— don't publish anything silently.

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
| `radicale/` | self-hosted CalDAV server (config, launcher, user + list bootstrap) + OWNER-SETUP |
| `docs/BRIDGE-INSTRUCTIONS.md` | phone-side + PC-side prompt templates |
| `examples/` | one ready config per transport |
| `.archive/` | superseded material (the former multi-project layer, deep-dive docs) |

## Status

Working single-project version. Known rough edges: no packaged installer / Task-Scheduler
auto-start yet; the pyicloud private API can change under Apple (the Radicale bus is the
documented fallback if it does). The former multi-project machinery lives in `.archive/`.
