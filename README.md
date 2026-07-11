# voice-bridge

Talk to a **Claude Code session on your PC from your phone** — by voice or text —
using **Apple Reminders (or any CalDAV list) as a shared message bus**. The Claude
iPhone app drops a task into a list; a small poller on the PC picks it up, hands it
to your local Claude Code "manager" session, and the manager's replies come back to
a second list on your phone. Long content (designs, docs) is delivered as a tappable
gist link plus a spoken walkthrough.

It is the Windows-friendly generalization of the `brianharms/reminder-watch` (macOS
AppleScript) pattern, lifted out of one project so **one install can serve many
projects** via a per-project JSON config.

## This channel is ASYNCHRONOUS — not a live call

Read this before building anything on top of the bridge. It is **delayed,
turn-based message-passing**, NOT a real-time connection:

- A reply the manager writes can reach the phone **a full turn (or more) later** —
  in the Claude voice app, the phone only checks the list on *its* turn, so a reply
  sent mid-thought surfaces the next time you speak.
- Therefore **every reply is stamped with a local `[HH:MM]` time** (added by
  `send_reply`), so both the owner and the phone-side voice assistant can tell how
  old a message is.
- When several replies are waiting, **the newest supersedes older ones on the same
  topic** — never act on a stale instruction a later message already replaced. This
  rule is baked into the phone-side prompt in `docs/BRIDGE-INSTRUCTIONS.md`.

Design docs, prompts, and any automation built on this MUST NOT assume instant
round-trips.

## How it works

```
 iPhone (Claude app / Reminders)          PC (Claude Code manager session)
 ┌───────────────────────────┐            ┌────────────────────────────────────┐
 │  inbox list  "To Claude"  │ ─ phone →  │  poller (pyicloud_bridge.py)        │
 │                           │            │    └─ appends to  to-manager.md ────┼─▶ manager reads
 │  output list "From Claude"│ ◀─ PC ───  │  drains  to-phone.md → output list ◀┼── manager appends reply
 └───────────────────────────┘            └────────────────────────────────────┘
        (timestamped, async — a reply may land a turn later)
```

- **Primary transport — `pyicloud_bridge.py`:** talks to Apple's modern CloudKit
  Reminders store (what the iPhone + Claude app actually use) via the pyicloud
  private web API. Reads/writes the two lists directly. Needs a one-time 2FA login
  (`pyicloud_login.py`) that caches a trusted session for ~60 days.
- **Alt transport — `reminder_bridge.py` + `radicale/`:** a CalDAV poller. iCloud's
  own CalDAV can't see CloudKit lists on a modern account (`probe.py` tests this),
  so the robust alt is a tiny self-hosted **Radicale** CalDAV server both the phone
  and PC see. Same mailbox contract, one env var swap. See `radicale/OWNER-SETUP.md`.

Both bridge into the same **file mailbox** (`~/.claude/message-protocol/` by
default): inbound reminders become lines in `to-manager.md`; the manager replies by
appending to `to-phone.md`, which the warm loop drains into the output list.

## Quickstart (for THIS repo's project, project-proposals)

```bash
# 1. Drop the config into the project you want to bridge (or point env at it):
cp examples/project-proposals.voice-bridge.json /path/to/project/.claude/voice-bridge.json
# ...or:  export VOICE_BRIDGE_CONFIG=/abs/path/to/voice-bridge.json

# 2. One-time: put creds under ~/.auth and seed the trusted session (see below).
uv run pyicloud_login.py            # interactive 2FA the first time only

# 3. Run the poller from the project's cwd (auto-finds ./.claude/voice-bridge.json):
uv run pyicloud_bridge.py --interval 60
```

Inspect / test without any network:

```bash
uv run pyicloud_bridge.py --show-config    # print the fully resolved settings
uv run pyicloud_bridge.py --dry-run        # config + the exact to-manager.md line, no network
uv run pyicloud_bridge.py --once           # single poll; exit 1=nothing new, 0=new items
uv run pyicloud_bridge.py --selftest       # LIVE end-to-end proof into a temp mailbox
uv run pyicloud_bridge.py --reply "text"   # write one output-list reminder (timestamped)
```

`uv run` reads each script's PEP-723 inline deps and installs them into an
ephemeral env — no venv to manage.

## Config schema

A project declares one JSON file. It is resolved in this order (first that exists):

1. `--config PATH` on any script
2. `$VOICE_BRIDGE_CONFIG`
3. `./.claude/voice-bridge.json` (relative to the current working directory)
4. none found → built-in defaults

Every field is optional and falls back to the default below, so a minimal config
(even `{}`) works. `~` is expanded in every path. **No secrets go in this file** —
only the *locations* of the credential/cookie/topic files under `~/.auth`.

| field | default | meaning |
|-------|---------|---------|
| `name` | `"voice-bridge"` | project label; also namespaces the seen-files |
| `inbox_list` | `"To Claude"` | list the phone writes to (phone → PC) |
| `output_list` | `"From Claude"` | list replies go to (PC → phone) |
| `from_name` | `"owner-phone"` | tag in each mailbox line `- [HH:MM] (from_name) …` |
| `mailbox_dir` | `"~/.claude/message-protocol"` | the manager's file mailbox |
| `to_manager` | `"to-manager.md"` | inbound file (relative to `mailbox_dir`) |
| `to_phone` | `"to-phone.md"` | reply file the loop drains (relative to `mailbox_dir`) |
| `ntfy_topic_file` | `"~/.auth/ntfy-topic.txt"` | file holding the ntfy topic |
| `creds_env` | `"~/.auth/icloud.env"` | `KEY=value` creds file |
| `cookie_dir` | `"~/.auth/pyicloud-cookies"` | pyicloud trusted-session cache |
| `poll_interval` | `10` | loop cadence (seconds) |

Derived (not set directly): the dedupe seen-files live at
`<mailbox_dir>/state/<slug(name)>-seen.txt` and `…-reply-seen.txt`, so several
projects can share one `mailbox_dir` without colliding.

See `examples/project-proposals.voice-bridge.json` for a ready file matching this
repo's live setup ("To Claude" / "From Claude", default mailbox + `~/.auth` creds).

## How a project consumes it

1. Copy `examples/…json` to the project's `.claude/voice-bridge.json` (or set
   `VOICE_BRIDGE_CONFIG`), editing `name` and any list names.
2. Ensure `~/.auth` holds the creds + topic (below) and the session is seeded.
3. From the project's directory: `uv run /path/to/voice-bridge/pyicloud_bridge.py --interval 60`.
   The poller finds the config in the cwd's `.claude/` automatically.
4. Give the manager session the **PC-side prompt** and the phone the **phone-side
   prompt** from `docs/BRIDGE-INSTRUCTIONS.md`.

## Credentials — `~/.auth` (never in the repo)

The bridge reads secrets only from files under `~/.auth`; nothing secret is ever
committed or printed (only a masked `apple_id=d***@… (hidden)` is logged).

- `~/.auth/icloud.env` — the pyicloud creds:
  ```
  ICLOUD_APPLE_ID=you@icloud.com
  ICLOUD_PASSWORD=your-MAIN-apple-id-password
  ```
  (pyicloud needs the **main** Apple ID password, not an app-specific one. For the
  CalDAV alt transport it instead reads `ICLOUD_APP_PASSWORD` — an app-specific
  password — and optional `ICLOUD_CALDAV_URL`.)
- `~/.auth/pyicloud-cookies/` — the trusted-session cache written by
  `pyicloud_login.py`. First login needs a 6-digit 2FA code; drop it into
  `~/.auth/2fa_code.txt` (the manager writes it once the owner reads it off a
  trusted device). Good for ~60 days, then re-run `pyicloud_login.py`.
- `~/.auth/ntfy-topic.txt` — one line: your private ntfy.sh topic (for phone banners).

Full owner walkthrough: `docs/OWNER-SETUP.md`.

## The ntfy + gist content-delivery pattern

Reminders can't fire a real push on their own, and the voice assistant can't open a
private link. `deliver_content.sh <markdown-file> "one-line summary"` therefore does
all three at once:

1. publishes the Markdown as an unlisted **GitHub gist** (renders on phone +
   desktop) — the readable artifact;
2. pushes a **tappable ntfy notification** whose tap opens the gist (a real banner
   with sound); and
3. drops the **full text + link into the output list** (`notify=False`, no dup beep)
   so the phone assistant can read a short spoken walkthrough aloud.

`gh` must be authed; the ntfy topic comes from the config's `ntfy_topic_file`.

## Files

| path | role |
|------|------|
| `config.py` | config resolution + `~` expansion + `python config.py [PATH]` to print resolved settings |
| `pyicloud_bridge.py` | **primary** transport (CloudKit private API): poll, reply, drain, `--dry-run`/`--selftest`/`--show-config` |
| `pyicloud_login.py` | one-time 2FA login → cached trusted session |
| `deliver_content.sh` | gist + ntfy + voice content delivery |
| `_caldav.py` | shared CalDAV plumbing for the alt transport (creds, connect, list discovery) |
| `probe.py` | GO/NO-GO CalDAV feasibility probe (read-only) |
| `reminder_bridge.py` | **alt** transport (CalDAV/Radicale): same contract, different bus |
| `radicale/` | self-hosted CalDAV server (config, launcher, user + list bootstrap) + its OWNER-SETUP |
| `examples/project-proposals.voice-bridge.json` | ready config matching this repo's live setup |
| `docs/BRIDGE-INSTRUCTIONS.md` | the phone-side + PC-side prompt templates (async rules included) |
| `docs/OWNER-SETUP.md` | owner credential + list + login walkthrough |

## Status

Rough working version — runnable and generic. Known rough edges: no packaged
installer / Task-Scheduler auto-start yet; the pyicloud private API can change under
Apple; the CalDAV alt transport is the documented fallback if it does.
