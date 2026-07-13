# voice-bridge — post-migration roadmap

The package is built (see `voice-bridge.md`). This is the next body of work: unify the
Radicale server into the CLI, then a reliability + operability pass. Each phase is its own
commit(s) on PR #7, test-first, kept green.

Full visual spec: the 📚 full-software-spec / 🎯 target-graph artifacts. Adds 4 modules
(`server`, `log`, `status`, `deliver`) → 19 total, still an acyclic DAG (config-sink, cli-source).

## P0 · radicale-server (fold `radicale/` into the package)

- **`voice_bridge/server.py`** — `init` (write config + bcrypt htpasswd user + seed `radicale.env`),
  `start(background=)`, `stop`, `status`, `client_url`, `is_reachable`, `ensure_running(spawn_child=)`.
- **config**: `radicale_host` (0.0.0.0), `radicale_port` (5232, int), `radicale_user` (vox).
- **cli**: `radicale-server {init,start,stop,status,url}`; `run --with-server`.
- **runner**: radicale + not reachable → `--with-server` spawns a child (killed on exit), else prints a hint.
- **setup**: radicale → `server.init`.
- **delete** the root `radicale/` dir (config/run_radicale/make_user/start-radicale/OWNER-SETUP → folded).
- Tests: files+bcrypt+creds (unit), real start/stop/reachable + CalDAVTransport against the self-launched
  server (integration), port-in-use/stale-pid/no-server-hint (bad).

## P1 · reliability

- **`voice_bridge/log.py`** — `configure(verbose,quiet,logfile)`, `get()`; replace operational prints;
  cli `-v/-q/--log-file`.
- **poller.run** — resilient loop: catch transport errors, exponential backoff, retry; `--once` no retry;
  eject in `finally`.
- **reply cursor** — `mailbox.read_new_lines(path, cursor_file)` + `load/save_cursor`; `drain_replies`
  uses a byte-offset cursor (robust to truncation, bounded state). Config `reply_cursor_file`.
- **seen compaction** — `mailbox.compact_seen(path, live_ids)`; `poll_inbox` GCs stale ids.

## P2 · operability

- **`voice_bridge/status.py`** — `gather(cfg) -> dict`; `run` writes `poller.pid`. cli `status [--json]`.
- **tail** — cli `tail [--box] [-f]` (cross-platform, reuses `read_new_lines`).
- **`--json`** — `lists`/`peek`/`status`/`config show`.
- **send** — cli `send "text" [--no-notify]` → `poller.send_reply`.

## P3 · CI/quality

- mypy job (`mypy voice_bridge`) + `pytest-cov` floor (ratcheted).

## P4 · hardening

- **`voice_bridge/deliver.py`** — `deliver(cfg, file, *, summary, public)`: confirm → `gh gist create
  --private` → `ntfy.push(click=url)`.
- **icloud throttle backoff** — `_retrying` wrapper: 503 → backoff/retry, bounded.

## Good/bad/edge test paths

Every capability has good ✓ / bad ✗ / edge ◐ rows against existing oracles (FakeTransport,
embedded Radicale, fake ntfy, mocked gh/pyicloud, `caplog`) — see the 📚 full-software-spec
artifact for the per-module catalog. No new test infrastructure required.
