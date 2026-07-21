# HANDOFF — resume plan (voice-bridge)

New here? Read `STATE.md` (where the project is) first, then this file (what to do next),
then `PITFALLS.md` (gotchas) and `docs/.design/voice-bridge.md` (architecture) +
`docs/.design/roadmap.md` (what shipped). This file assumes no memory of the prior session.

Current state lives in **`STATE.md`** — not repeated here to avoid drift. One-line summary:
PR #7 (`feat/voice-bridge-package` → `master`) has the full package built, 97 tests +
CI green; iCloud has never been exercised against a real account.

## Verified vs NOT verified  (THIS is why we're resuming)

| path | state |
|---|---|
| Radicale transport | integration-tested against real embedded + self-launched Radicale ✓ |
| Config / mailbox / poller / cursor / dedupe / ntfy shape | unit + contract ✓ |
| CLI dispatch / exit codes | e2e (in-process) ✓ |
| **iCloud transport, LIVE** | **NOT tested — deliberate (2FA + private API can't run in CI)** |
| **A real phone round-trip (either transport)** | **NOT tested — needs a device** |

**This is still a DESIGN session in progress.** The next session continues the
collaborative design with the user — it is NOT a pure execution run. Anything marked
**DRAFT** below is an unreviewed proposal: discuss and agree it with the user *before*
building. The overall goal we are working toward is **ensuring all functionality works
end-to-end, starting with the iCloud integration** (the biggest untested seam).

## Resume plan — design + e2e verification, iCloud first

### Step 0 — design the icloud-login upgrade WITH the user (design topic — not agreed yet)
> **DRAFT / NOT DESIGNED.** The idea below was drafted solo and has NOT been discussed or
> agreed. It's a starting point for the design conversation, not a spec to implement.
> Resume by talking it through with the user first.

The intent: `icloud-login` should become a full, secure credential *setup* (input the
Apple ID + password safely), not just accept the 2FA code. Open questions to settle with
the user before any code:
- Interactive prompt vs flags; how the Apple ID is entered.
- How the password is entered **securely** — `getpass` (no echo) for interactive,
  `--password-stdin` for pipes; the draft assumes NO `--password` argv flag (leaks to the
  process list) — confirm that constraint.
- Where/how creds are stored and permissioned (`cfg.creds_env`, `0600` on POSIX; Windows?).
- Re-entry / rotation behaviour (a `--force`?), and idempotency when already set up.

Draft signature (for discussion only, expect it to change):
`icloud_login(cfg, *, apple_id=None, password_stdin=False, code=…, code_file=…, code_stdin=…, force=False) -> int`
with a `_ensure_creds(...)` helper. Once the design is agreed, spec its good/bad tests and
build it — it's the entry point that unblocks every iCloud e2e step below.

### Step 1 — iCloud live login (needs a real Apple ID + trusted device)
> Today's `icloud-login` only handles the 2FA code — it reads the Apple ID + password from
> the creds file, it does NOT prompt for them. So EITHER build Step 0's upgrade first, OR
> (to test right now) hand-write the creds file: put `ICLOUD_APPLE_ID=…` and
> `ICLOUD_PASSWORD=…` (the **MAIN** Apple ID password, not app-specific, for pyicloud) into
> `<state_dir>/icloud.env` (default `~/.local/state/vox-mailbox/icloud.env`).

1. `voice-bridge --config <proj>/.claude/voice-bridge.json icloud-login`
   → supply the 6-digit 2FA code (via prompt, `--code`, `--code-file`, or `--code-stdin`).
2. Confirm the trusted session cached under `<state_dir>/pyicloud-cookies/`.

### Step 2 — provision + doctor
3. On the iPhone Reminders app, create the two lists EXACTLY: `Vox-Message-Inbox`,
   `Vox-Message-Outbox` (iCloud can't create them via API).
4. `voice-bridge doctor` → expect GREEN for creds/auth/lists.
5. `voice-bridge lists` → confirm both lists appear; if duplicate-title ghosts show,
   pin GUIDs via `config set inbox_list_id/output_list_id`.

### Step 3 — round-trip, one direction at a time
6. `voice-bridge run --once` → confirm it connects + polls without error.
7. Dictate/ add a reminder in `Vox-Message-Inbox` on the phone → `voice-bridge run --once`
   → confirm a line appears in `<mailbox>/to-manager.md` and the reminder is completed.
8. `voice-bridge send "hello from PC"` → confirm it lands in `Vox-Message-Outbox` on the
   phone AND fires an ntfy banner (needs `state_dir/ntfy-topic.txt`).
9. Full loop with a real manager: paste `vox-prompt` into the Claude iOS app; run
   `voice-bridge run` (background) + a Claude Code manager reading `to-manager.md`.

### Step 4 — resilience / edges
10. Let the session lapse (or `--force` re-login) → confirm the 2FA re-login path.
11. Watch for throttle/503 (multi-poll) → confirm `_retrying` backoff, no re-2FA storm.
12. `voice-bridge status` / `tail -f` while it runs → confirm observability.

### Step 5 — Radicale live (already integration-green; verify with a real phone)
13. `radicale-server init` + `start --background`; add the CalDAV account on the phone
    over the tailnet (`radicale-server url`); repeat Steps 2–4 with `--transport radicale`.

## How to run

- Tests: `uv run --extra dev pytest`  (add `--cov=voice_bridge` for coverage).
- Lint/type: `uv run --extra dev ruff check .` · `uv run --extra dev mypy`.
- The CLI: `uv run voice-bridge <cmd>`  (or `uvx --from git+<url> voice-bridge <cmd>`).
- iCloud needs the `[icloud]` extra (`pyicloud`) installed at runtime; Radicale server the
  `[server]` extra (`radicale`, `bcrypt`).

## Open threads (not blocking)
- Coverage floor is 79 (ratchet up as e2e adds tests).
- `caldav.py` `_cal` has a convoluted `resolve_list(...) and ...` line (works; tidy later).
- ntfy server host `ntfy.sh` default; self-host via `ntfy_server`.
