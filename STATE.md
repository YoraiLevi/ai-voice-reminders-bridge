# STATE — voice-bridge

Snapshot of where the project actually is. New here? Read this, then `HANDOFF.md`
(what to do next), `PITFALLS.md` (gotchas), and `docs/.design/` (architecture).

## What this is
`voice-bridge` is a Python CLI that adds an async **voice spoke** (named `vox`) to a
file-mailbox agent system: you talk to your PC-side agent from your phone through Apple
Reminders (iCloud) or a self-hosted Radicale CalDAV server. It is an *extension* of the
file-mailbox protocol — it does not own the mailbox or the manager, only the phone spoke.

## Current state (as of this session)
- **All code lives in the `voice_bridge/` package** — 19 modules, acyclic import DAG
  (`config` is the sink, `cli` the entry). Installable; runnable via
  `uv run voice-bridge …` or `uvx --from git+<url> voice-bridge …`.
- **Work is on PR #7** — branch `feat/voice-bridge-package` → base `master`
  (push-locked; all work goes through the PR). Not yet merged.
- **Shipped this arc:** distillation of the old repo → migration of 7 legacy scripts into
  the package (deleted) → `radicale-server` (folded the old `radicale/` dir into
  `voice_bridge/server.py`) → a reliability/operability roadmap (P0–P4: logging, resilient
  loop, byte-cursor reply dedup, seen compaction, status/tail/send/--json, mypy+coverage
  CI, deliver, iCloud throttle backoff).
- **Quality gates green:** 97 tests (unit / contract / integration / e2e), `ruff` clean,
  `mypy` clean, coverage ~80%. CI matrix ubuntu+windows × py3.11–3.13 + lint + coverage —
  **green** (a cross-OS mypy bug was fixed in `934434d`).

## What is NOT verified (deliberately)
- **Live iCloud** — never touched a real Apple account (2FA + private API can't run in CI).
  iCloud path coverage is via `FakeTransport`; `ICloudTransport` has only a field-mapping
  unit test.
- **A real phone round-trip** — needs a device; not done for either transport.

These two are the point of the *next* session (see `HANDOFF.md`).

## Where things live
- Architecture spec: `docs/.design/voice-bridge.md`. What shipped: `docs/.design/roadmap.md`.
- Run tests: `uv run --extra dev pytest`. Lint/type: `uv run --extra dev ruff check .` /
  `uv run --extra dev mypy`.
- Runtime extras: `[icloud]` (pyicloud), `[server]` (radicale, bcrypt), `[caldav]`.
