# PROTOCOL — the voice-bridge contract

This is the **single source of truth** for how the bridge behaves. If any other doc
disagrees with this file, this file wins. It is deliberately short: the whole system
is "two Reminders lists + a file mailbox + a poller," and its reliability comes from a
handful of invariants, all stated here.

## 1. The bus

A **file mailbox** is the durable heart of the system — a directory (default
`~/.claude/message-protocol/`) holding two append-only Markdown files:

- `to-manager.md` — inbound. The poller appends each new phone request here.
- `to-phone.md` — outbound. The manager appends replies here; the poller drains them
  to the phone's output list.

Append-only + on-disk = **durable**: the poller or the Claude Code manager can crash or
restart and nothing in flight is lost. To see all traffic, just read the two files.

## 2. Message grammar

Every mailbox line is exactly one physical line:

```
- [HH:MM] (<from_name>) <text>
```

`[HH:MM]` is a local timestamp stamped at write time. `<from_name>` (default
`owner-phone`) tags the origin. Embedded newlines in `<text>` are flattened so one
message is always one line.

## 3. Async rules — this is NOT a live call

The channel is **delayed, turn-based message-passing**. Build nothing that assumes an
instant round-trip.

- A reply the manager writes can reach the phone **a full turn (or more) later** — in
  the Claude voice app the phone only checks the list on *its* turn.
- Every message is **timestamped** so both sides can judge how old it is.
- **Newest supersedes.** When several messages stack up on the same topic, the newest
  wins; never act on a stale instruction a later message already replaced.

## 4. The one reply path

Replies MUST go through the bridge's `send_reply` (via `--reply`, or by appending a line
to `to-phone.md` that the running poller drains). That path creates the output-list item
**and** fires the ntfy banner. A raw CalDAV/Reminders write would NOT notify — so never
bypass `send_reply`.

## 5. Transport contract — two buses, one mailbox

The mailbox contract above is identical across both transports; only the bus differs:

- **iCloud** (`pyicloud_bridge.py`) — the **default**. Apple's CloudKit Reminders via the
  pyicloud private API. Needs Apple ID + password in `~/.auth/icloud.env` and a one-time 2FA
  login (`pyicloud_login.py`), cached ~60 days. Fast; cannot create lists (make them by hand
  on the phone).
- **Radicale** (`reminder_bridge.py` + `radicale/`) — a self-hosted CalDAV server both the
  phone and PC see. No Apple password and can create lists (self-provisioning via
  `bootstrap.py --transport radicale`), but slower to sync and needs a VPN/tunnel + a CalDAV
  account on the iPhone.

Switching buses is a `creds_env` swap in the config — the manager side is unchanged.

## 6. Reliability invariants

- **Idempotent delivery.** The poller records processed items in seen-files
  (`<mailbox_dir>/state/…-seen.txt`), so no reminder or reply is ever delivered twice —
  even across restarts.
- **Ghost-list immunity.** A list may be pinned by its exact CloudKit record id
  (`inbox_list_id` / `output_list_id`) to dodge orphaned duplicate lists that share a
  title but linger server-side.
- **Idempotent provisioning.** `bootstrap.py` is safe to re-run: it detects an existing
  config (and, for Radicale, existing lists) and reports rather than duplicating.
- **Verify before trust.** `probe.py` (does CalDAV work on this account?) and the
  bridges' `--dry-run` / `--selftest` / `--show-config` let you confirm behavior rather
  than hope.

## 7. Limits — when to graduate

Honest boundaries, so nothing is built on a false promise:

- **No delivery guarantee.** Delivery is by convention (append + poll), not a confirmed,
  loss-proof queue. There is no ack.
- **Single machine, single project.** One mailbox, one manager. Multi-project routing was
  intentionally removed; it lives in `.archive/` if ever needed again.
- **The private API can shift.** Apple can change the pyicloud path under you — that is
  exactly why the Radicale bus exists as a same-contract fallback.

The **prompt templates** that put these rules into the phone-side and PC-side agents live
in [`docs/BRIDGE-INSTRUCTIONS.md`](docs/BRIDGE-INSTRUCTIONS.md). Setup is
[`SETUP.md`](SETUP.md).
