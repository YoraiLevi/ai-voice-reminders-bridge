# HANDOFF — resume plan (voice-bridge)

Read this + `PITFALLS.md` + `docs/.design/voice-bridge.md` (architecture) and
`docs/.design/roadmap.md` (what shipped) to pick up cold.

## Where things stand

- **PR #7** (`feat/voice-bridge-package` → `master`, push-locked base so work via PR).
  The whole arc landed here: distillation → package migration → radicale-server → the
  reliability/operability roadmap (P0–P4).
- **Package:** `voice_bridge/` — 19 modules, acyclic DAG (`config` sink, `cli` source).
- **Tests:** 97 green — unit / contract (FakeTransport) / integration (embedded +
  self-launched Radicale) / e2e (in-process cli). `ruff` + `mypy` clean, coverage ~80%.
- **CI:** matrix ubuntu+windows × py3.11–3.13 + lint(ruff+mypy) + coverage. Was red on a
  cross-OS mypy bug (Windows-only `subprocess.CREATE_NEW_PROCESS_GROUP`); fixed with
  `getattr` (commit 934434d). Confirm the run is green on resume.

## Verified vs NOT verified  (THIS is why we're resuming)

| path | state |
|---|---|
| Radicale transport | integration-tested against real embedded + self-launched Radicale ✓ |
| Config / mailbox / poller / cursor / dedupe / ntfy shape | unit + contract ✓ |
| CLI dispatch / exit codes | e2e (in-process) ✓ |
| **iCloud transport, LIVE** | **NOT tested — deliberate (2FA + private API can't run in CI)** |
| **A real phone round-trip (either transport)** | **NOT tested — needs a device** |

The break-resume goal: **ensure all functionality works end-to-end, starting with the
iCloud integration** (the biggest untested seam).

## Resume plan — e2e verification, iCloud first

### Step 0 — implement the icloud-login upgrade (do this first)
Turn `icloud-login` into a full secure credential setup (design below). It's the entry
point to every iCloud e2e step; without it, seeding creds by hand is the blocker.

Design:
- `icloud_login(cfg, *, apple_id=None, password_stdin=False, code=None, code_file=None,
  code_stdin=False, force=False) -> int`
- `_ensure_creds(...)`: resolve Apple ID (`--apple-id` or prompt) + password
  (`--password-stdin` for pipes, else `getpass` — NEVER a `--password` argv flag) → write
  `cfg.creds_env` with `0600` (POSIX; best-effort Windows). Skip if present unless `--force`.
- Then existing 2FA (`resolve_code`) + session cache.
- Tests: apple-id+password-stdin writes creds (0600 on POSIX); mocked prompt path;
  present+no-force skips; empty → exit 2.

### Step 1 — iCloud live login (needs a real Apple ID + trusted device)
1. `voice-bridge --config <proj>/.claude/voice-bridge.json icloud-login`
   → enter Apple ID + **MAIN** Apple ID password (not app-specific for pyicloud) + 2FA code.
2. Confirm the trusted session cached under `state_dir/pyicloud-cookies/`.

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
