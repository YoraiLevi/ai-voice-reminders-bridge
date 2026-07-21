# PITFALLS — hard-won gotchas

## CI / cross-OS
- **mypy is platform-specific.** `subprocess.CREATE_NEW_PROCESS_GROUP`/`DETACHED_PROCESS`
  exist only on Windows; mypy on the Linux CI runner flags them `[attr-defined]` even
  though local (Windows) mypy passes. Access Windows-only stdlib constants via
  `getattr(subprocess, "NAME", 0)`. (This was the P4 CI red.)
- Run `uv run --extra dev mypy` mentally as "Linux mypy" — don't trust a local Windows pass.

## iCloud (pyicloud) — expect these during e2e
- **Password:** pyicloud needs the **MAIN Apple ID password**, NOT an app-specific one.
  (The CalDAV/Radicale path is the opposite — it needs an app-specific password on iCloud.)
- **Priming:** `list(r.lists())` MUST be called once before `list_reminders()` or the
  service 400s. (Handled in `ICloudTransport.connect`.)
- **Cannot create lists:** iCloud exposes no create-list API — the two lists must be made
  by hand in the iPhone Reminders app. `create_list` raises `NotSupportedError` by design.
- **Ghost lists:** deleting+recreating a list on the phone leaves same-titled orphans with
  new CloudKit ids; resolve-by-title is ambiguous. Use `voice-bridge lists` to see GUIDs
  and pin `inbox_list_id`/`output_list_id`.
- **2FA / session:** the trusted session lapses (~60 days or on password change). Watch for
  "session needs 2FA"; re-run `icloud-login`. Multiple pollers on one Apple ID historically
  caused a 503 + forced-re-2FA storm — `_retrying` backs off on 503, but avoid many pollers.
- **CalDAV 500 on server-side filters:** iCloud 500s on the "exclude completed" filter, so
  read all + filter completed client-side. (Handled in `CalDAVTransport._iter_objects`.)

## Radicale / server
- Empty CalDAV lists don't sync to the iPhone until they hold ≥1 item — `create_list` seeds
  a placeholder VTODO for this reason.
- A dangling Radicale index entry (points at a deleted resource) 404s on load; a bulk load
  would fail the WHOLE poll. `_iter_objects` loads per-item and skips bad ones.
- Reachability is over the tailnet (WireGuard); the server binds `0.0.0.0` on plain HTTP —
  do NOT expose that port publicly without TLS.

## Mailbox / dedupe
- Reply drain uses a **byte-offset cursor** (`reply_cursor_file`), not a line-index set —
  robust to truncation, holds a partial last line. Don't reintroduce line-index dedup.
- Inbox dedupe is by transport item id + completion; the seen-file is compacted to ids
  still readable each cycle (never drops an incomplete item).

## Testing
- Live Apple is deliberately NOT in CI (2FA). iCloud behavioral coverage comes via
  `FakeTransport`; `ICloudTransport` has only a field-mapping unit test.
- The embedded/self-launched Radicale integration tests are the real CalDAV oracle and run
  on both OSes; keep them.
