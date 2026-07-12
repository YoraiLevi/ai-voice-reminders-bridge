# Session changelog — 2026-07-11/12 (dual-channel Vox voice bridge)

Full compare (all changes this session):
`https://github.com/YoraiLevi/ai-voice-reminders-bridge/compare/c984c29...bc2d7be`
(Repo was renamed this session: `voice-bridge` → `ai-voice-reminders-bridge`.)

## Code changes — file by file

### `pyicloud_bridge.py` (the iCloud/fast transport) — the most-changed file
- **List id-pinning** — `config` gained `inbox_list_id`/`output_list_id`; `_require_list` resolves a list
  by exact CloudKit id, ignoring title. *Why:* iCloud keeps orphaned "ghost" lists that share a title; a
  title lookup silently picked the wrong (empty) one. `a439544`.
- **Auto-heal inbox resolution** — `_require_list(auto_heal=True)` picks the same-titled list with UNREAD
  items (the live one; ghosts are empty). *Why:* when the owner recreates "To Claude" it gets a new id and
  strands a pin; auto-heal self-heals with no manual re-pin. `ce561cd`.
- **Native alarm on every reply (option-B parity)** — `send_reply` sets a timed `due_date` so iOS fires a
  native banner. **TZ FIX:** use NAIVE LOCAL wall-clock (a tz-aware time fired one UTC-offset early, 3h at
  UTC+3). Also front-loads any link + sets the ntfy `Click` header. `c6e055f`.
- **`_notify_push` gained `click`** (one-tap link). `c6e055f`.

### `reminder_bridge.py` (the CalDAV/Radicale-backup transport)
- **Option-B native alarm on every reply** — VTODO due-time + DISPLAY VALARM + link front-load + ntfy
  Click; `native_alarm` kwarg (default on). `da4dc65`.
- **Poll hardening** — `_incomplete_todos` now loads each item individually and SKIPS un-loadable ones.
  *Why:* a dangling Radicale collection entry (deleted `.ics` still indexed) 404'd the bulk load and
  silently blocked the ENTIRE inbox poll. `a439544`.

### `config.py`
- Added `inbox_list_id` / `output_list_id` config fields (optional, for id-pinning). `a439544`.

### `vox_instructions.py` — NEW file
- The global **"Vox Instructions" list**: GLOBAL RULES (sourced from the phone prompt), ROUTING TABLE,
  BOOTSTRAP LINE, and LIVE STATE items — each overwritten wholesale on update. `5a468ec`, `cefa506`,
  `fa967a7`.
- **Concurrency-safe routing** via ETag If-Match CAS + retry, so parallel project onboards don't clobber
  each other; returns a verified confirmation. `18f6103`.

### `radicale_bootstrap.py`
- On onboarding, auto-registers the project's row into the Vox routing table (best-effort). `5a468ec`.

### `docs/BRIDGE-INSTRUCTIONS.md` — the Vox PHONE-SIDE PROMPT (instruction changes)
- Named the assistant **"Vox"**. `fb3d487`.
- **Poll the From-list 2-3×** (Radicale sync latency is inconsistent). `140cdf5`.
- On an explicit **"check again", always do a fresh live re-check** (never answer from memory). `85c4ea0`.
- **Auto-fetch missing CONTEXT** (not just content) from the relevant agent via the routing table. `99c4004`.
- Documented the **Vox Instructions list** + the one manual bootstrap line.

### `docs/ARCHITECTURE.md` — NEW
- Full plain-spoken architecture (components, data flow, auto-heal). `bc2d7be`.

## Config / runtime changes (in the project-proposals repo, `.claude/`)
- `voice-bridge.json` (iCloud): pinned `inbox_list_id`/`output_list_id` to the live iCloud lists; distinct
  `mailbox_dir` = `~/.claude/message-protocol/icloud`.
- `radicale.voice-bridge.json`: `inbox_list`/`output_list` → **"To Backup"/"From Backup"** (renamed).
- `w3.voice-bridge.json` — NEW (w3's own voice channel).
- Runtime (not files): iCloud "To Claude"/"From Claude" pinned; Radicale lists **renamed** To/From Backup;
  Vox Instructions **migrated to iCloud** (old Radicale list deleted); pollers reconfigured (iCloud fast +
  Radicale backup).

## Project-proposals repo
- `CLAUDE.md` + `.meta/live-state.md`: formalized the **manager LIVE STATE protocol** (grounded in PM
  best-practice research). `be4cc238` (private repo `YoraiLevi/project-proposals`).

## PENDING — to graduate this properly (not yet done)
1. **`vox_instructions.py` is CalDAV-only.** The canonical Vox Instructions list is now on iCloud, but the
   self-provisioning tooling still writes to Radicale/CalDAV. Needs an iCloud-aware path (or a documented
   split). *This is the biggest gap.*
2. **Dual-send isn't a single helper.** Alarm-worthy replies are sent to iCloud + backup by hand; should be
   one `send_reply` that fans out.
3. **`TROUBLESHOOTING.md` not written** — the foreseeable-bug/troubleshooting review Yorai asked for
   (ghost-list churn, dangling-entry 404, ~60-day 2FA re-trust, the tz gotcha, one-poller-per-account).
4. **Auto-heal is inbox-only.** "From Claude" recreation isn't auto-healed (uses the pinned id).
5. **Onboarding docs** (`SETUP-RADICALE.md`, examples) still describe the single-channel model — need
   updating for the dual-channel + id-pin design.
