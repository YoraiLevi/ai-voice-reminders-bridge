# Vox voice bridge — troubleshooting & foreseeable bugs

Hard-won gotchas from building the dual-channel system. Each: **symptom → cause → fix → how to
diagnose.** A new agent should read this before debugging the bridge.

## Channel model (get this right before debugging routing)
Radicale is the ONE CalDAV system and hosts **two distinct kinds of channels**: (1) the MANAGER's
*fallback* lists "To Backup"/"From Backup" (used only if the iCloud fast lane fails, + native alarms) —
the manager's PRIMARY is iCloud+ntfy; and (2) the **worker channels** "To W3"/"From W3" etc., which live
on Radicale **permanently as each worker's ONLY channel** (workers never touch iCloud — that keeps a
single iCloud poller and avoids the multi-poller 2FA/503 storm). So it is NOT "iCloud primary, Radicale
backup" — Radicale is the single home for BOTH the manager's fallback AND all worker channels. Full
picture: `ARCHITECTURE.md`.

## 1. Messages silently stop arriving (the whole poll dies on one bad item)
- **Symptom:** the CalDAV poller logs `NotFoundError 404` every cycle; the owner's messages stop
  reaching the manager, but SENDING replies still works.
- **Cause:** a Radicale collection kept a **dangling index entry** — a deleted `.ics` still listed in
  the collection. `objects(load_objects=True)` bulk-loads every item and 404s on the ghost, which
  aborts the ENTIRE poll, so no message is bridged.
- **Fix (shipped):** `reminder_bridge._incomplete_todos` loads each item individually and **skips**
  any that error on load (commit `a439544`).
- **Diagnose:** enumerate the list with `objects(load_objects=False)`, then `.load()` each in a
  try/except and print the one whose URL 404s.

## 2. The "ghost list" problem (recreating a Reminders list strands the poller)
- **Symptom:** the owner sees his messages in "To Claude" but the manager never gets them; the poller
  is reading an empty list. Multiple "To Claude" ids appear via the API though the phone shows one.
- **Cause:** deleting + recreating a list on the phone gives it a **brand-new CloudKit id**; the old
  one lingers as an **invisible ghost** (the phone UI hides it, the raw API still returns it). A poller
  pinned to the old id reads the wrong, empty list.
- **Fix (shipped):** `pyicloud_bridge._require_list(auto_heal=True)` picks the same-titled list with
  **unread items** (the live one; ghosts are empty), with the pinned id as fallback (`ce561cd`,
  `a439544`).
- **Diagnose:** list every "To Claude"/"From Claude" with its id + pending count; the live one has the
  unread items.
- **Prevention:** don't delete+recreate the To/From lists — clear items inside them instead.

## 3. iCloud session needs 2FA (~every 60 days)
- **Symptom:** the iCloud poller errors `pyicloud session needs 2FA`; nothing bridges on the fast channel.
- **Cause:** Apple expires the cached trusted session (~60 days).
- **Fix:** `uv run pyicloud_login.py --config <iCloud cfg>` — it prompts; the owner reads the 6-digit
  code off a trusted device and it's dropped into `~/.auth/2fa_code.txt`; the session re-trusts.
- **Diagnose:** `requires_2fa` / "needs 2FA" in the poller log.
- **NEVER** run multiple iCloud logins/pollers on one Apple ID at once — that triggers a repeated-2FA /
  503 throttle storm (the original "auth-storm" incident). **One iCloud poller per account.**

## 4. A native alarm fires at the wrong time (off by your UTC offset)
- **Symptom:** the iCloud reply's alarm fires exactly one timezone-offset early (e.g. 3h behind at UTC+3).
- **Cause:** a **tz-aware** datetime passed to pyicloud's `create(due_date=...)` gets mangled.
- **Fix (shipped):** use a **naive LOCAL wall-clock** time — `datetime.now() + timedelta(...)`, no
  `.astimezone()` (`c6e055f`).

## 5. The iCloud alarm shows but never *fires* a notification
- **Symptom:** the reminder displays a due-time/alarm, but no banner/sound at that time.
- **Cause:** Apple's private iCloud API (pyicloud) has **no time-alarm call** — `create()` only sets a
  `due_date`, which displays but doesn't alert. Only **CalDAV** (Radicale) can attach a firing VALARM.
- **Working-as-intended:** the FAST channel notifies via **ntfy** (instant, Apple-independent); the
  BACKUP/CalDAV channel is where native alarms come from. They're separate by design — don't try to make
  iCloud fire native alarms.

## 6. CalDAV → phone sync is slow and inconsistent (up to 5+ min)
- **Symptom:** replies on the Radicale channel reach the phone minutes late, unpredictably.
- **Cause:** CalDAV sync to iOS Reminders is polled, not pushed.
- **Mitigation:** iCloud is the fast primary; Vox's prompt tells it to re-check the From-list 2–3× before
  concluding it's empty (`140cdf5`).

## 7. Mailbox collision (a poller writing into the workers' channel)
- **Symptom:** the owner's phone messages and worker messages land in the same file.
- **Cause:** a config's `mailbox_dir` defaulted to the root `~/.claude/message-protocol`, colliding with
  the workers' `to-manager.md`.
- **Fix:** give each channel/project a **distinct `mailbox_dir`** (e.g. `.../icloud`, `.../radicale`,
  `.../<project>`).

## 8. pyicloud can't create / delete / rename LISTS (only reminders)
- **Implication:** the "To Claude"/"From Claude"/"Vox Instructions" lists must be **created once by
  hand** in the iOS Reminders app; ghosts can't be API-deleted (only their items). This is why the
  auto-heal + id-pin approach exists instead of "just delete the duplicates."

## 9. ntfy banner clips ~150 chars mid-word
- Keep the ntfy body short + word-boundary; the FULL reply text lives in the reminder itself, not the
  banner. (`_notify_push` already trims to a word boundary.)

## Quick health checks
- **Fast channel up?** `uv run pyicloud_bridge.py --config <iCloud cfg> --once` (exit 0 = new item(s),
  1 = nothing new; a 2FA/auth error surfaces here).
- **Which To Claude is live?** list all same-titled lists + pending counts (the live one has unread).
- **Radicale reachable?** connect via `_caldav` and `find_list` the inbox/output; a dangling-entry 404
  on read means run the item-by-item enumeration (see #1).
