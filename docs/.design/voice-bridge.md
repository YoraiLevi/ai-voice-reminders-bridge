# voice-bridge — target architecture (design spec)

Status: **design, pre-build.** This is the spec the package build targets. It supersedes the
script-based layout once the package lands; until then the scripts in the repo root are the
working implementation.

## 1. What it is

`voice-bridge` is a thin, self-bootstrapping CLI that attaches one async **voice spoke**
(named `vox`) to an existing **file-mailbox** agent system and federates it to a phone via
Apple Reminders. You dictate a Reminder; voice-bridge delivers it into the mailbox; a peer's
reply comes back as a Reminder plus a tappable ntfy banner.

It is an **extension** of the file-mailbox protocol
(https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox). It does **not** set
up the mailbox or the agents — it plugs a spoke into one that already exists (and can create a
bare mailbox if asked, but never a peer).

## 2. Boundary — owns vs defers

```
DEFER to the mailbox protocol        OWN in voice-bridge
  the mailbox + append-only files      the gateway spoke (poller) + its 3 edges
  the peer/agents (LLM sessions)       the phone transport (iCloud default / Radicale)
  join / liveness / mesh presence      prompts/vox.md (rendered) — the phone prompt
                                       a 3-line async note for the peer
```

The spoke's three edges — and the whole surface to abstract + test:
1. **the bus** (Transport): the two Reminders lists on iCloud/Radicale.
2. **the file mailbox**: read `to-vox.md`, write `to-manager.md` (routing target).
3. **the doorbell** (ntfy): push banners.

## 3. Vocabulary + topology-agnostic routing

No "manager" as an assumed role — voice-bridge talks to a **peer**, by default whoever reads
`to-manager.md` (the star hub), but it is just a routing target:

```
spoke_name = vox      → our inbox we READ:        to-vox.md       (messages TO us)
route_to   = manager  → the peer inbox we WRITE:  to-manager.md   (messages we SEND out)
from_name  = <spoke_name>  → every line/announcement we write is tagged "(vox)"
```

Default is the star (`route_to=manager`); set `route_to` to any spoke to route in a mesh once
the protocol grows presence. We do **not** implement mesh liveness — we stay agnostic so we
adjust when it lands.

## 4. Architecture (runtime)

Five independent runtimes, one clock. Only the poller is ours.

```
Vox (phone persona)   ← Claude iOS app + prompts/vox.md          (not ours)
the bus  (2 lists)    ← Apple CloudKit OR a Radicale process     (not ours)
the poller (spoke)    ← uv + Python — THE thing we own+run+test  ◀── ours
the peer  (does work) ← a Claude session joined to the mailbox   (not ours)
the banner            ← ntfy.sh or self-hosted                   (not ours)
```

The poller is a headless, no-AI daemon. Its `main` loop is the only clock:
`connect → poll_inbox (bus→to-manager.md) → drain_replies (to-vox.md→bus + ntfy) → sleep`.

Reliability is a property of the two **buffers** (the list, the file mailbox): durable
(append-only), idempotent (seen-files), decoupled (only coupling is "leave a note").

## 5. Config schema

Three concerns, separated by where things live. All fields overridable; `config --help` is the
source of truth.

| group | field | default | note |
|---|---|---|---|
| identity/routing | `spoke_name` | `vox` | our inbox `to-vox.md`; also `from_name` tag |
| | `route_to` | `manager` | peer inbox `to-manager.md` |
| | `from_name` | `= spoke_name` | mailbox-line tag `- [HH:MM] (vox) …` |
| phone bus (lists) | `inbox_list` | `Vox-Message-Inbox` | phone → us (dictations) |
| | `output_list` | `Vox-Message-Outbox` | us → phone (replies) |
| | `inbox_list_id` / `output_list_id` | `""` | GUID pins (ghost-list fix; set via `lists`) |
| shared mailbox (theirs) | `mailbox_dir` | `~/.agent-mail` | the protocol's bus |
| our state (ours) | `state_dir` | `$XDG_STATE_HOME/vox-mailbox` → `~/.local/state/vox-mailbox` | unique, conflict-free |
| | `creds_env` | `{state_dir}/icloud.env` | derived |
| | `cookie_dir` | `{state_dir}/pyicloud-cookies` | derived |
| | `ntfy_topic_file` | `{state_dir}/ntfy-topic.txt` | derived |
| transport | `transport` | `icloud` | `icloud` \| `radicale` |
| ntfy | `ntfy_server` | `https://ntfy.sh` | override for self-hosted |
| | `ntfy_title` / `ntfy_tags` / `ntfy_priority` | `Vox`/`robot`/`high` | banner presentation |
| | `ntfy_body_limit` | `-1` | banner body char cap; -1 = no clip |
| tuning | `reply_summary_limit` | `-1` | reminder title cap; -1 = no clip |
| | `poll_interval` | `10` | loop cadence (s) |

Resolution order: `--config` → `$VOICE_BRIDGE_CONFIG` → `./.claude/voice-bridge.json` →
defaults. `--set KEY=VALUE` overrides at provision time (validated; ints coerced).

## 6. CLI surface

| command | does |
|---|---|
| `run [--mailbox DIR] [--transport T] [--require-mailbox] [--yes] [--interval N] [--once] [--dry-run]` | ensure-everything-then-loop (state machine below) |
| `setup [--transport T] [--set K=V] [--yes]` | ensure config+auth+lists + phone instructions + **live end-to-end verification**, then stop |
| `doctor [--fix]` | survey every interface, GREEN/WARN/RED + guide/repair |
| `lists` | enumerate transport lists with **name + GUID** (pick the exact one) |
| `peek --box inbox\|outbox [-n N]` | show messages currently in a box |
| `config show \| get KEY \| set KEY VALUE` · `config --help` | read/edit values; `--help` = every field+default |
| `notify "…" [--click URL]` | push a banner |
| `icloud-login [--code C \| --code-file P \| --code-stdin]` | iCloud 2FA from prompt/file/pipe |
| `vox-prompt` | print the phone prompt, rendered with your list names |

### `run` state machine (self-bootstrapping)

```
1. MAILBOX   --mailbox → use;  else TTY → prompt (~/.agent-mail · ./.agent-mail · custom);  else default
2. FILES     missing → create (--require-mailbox → error instead)
3. CONFIG    none → run SETUP flow                 ⟲ reuse `setup`
4. AUTH      iCloud + no session → login           ⟲ reuse `icloud-login`
5. LISTS     missing/ambiguous → provision or pick  ⟲ reuse `lists`
6. JOIN      append "- [HH:MM] (vox) joined — async voice spoke" → to-manager.md
7. LOOP      poll inbox → to-manager.md ;  drain to-vox.md → outbox + ntfy
8. EJECT     on exit → append "(vox) stopping" + delete to-vox.md
```

Every ensure-step checks state first and does nothing if satisfied. `run` reuses the other
commands' code — one path per capability, invoked from both the explicit command and `run`.

`setup` = steps 1–5 + a live smoke test the user watches (probe item through `inbox_list` →
confirm it lands in `to-manager.md`; probe reply → confirm `output_list` + banner), then stop
(no loop). `run` = `setup` + loop.

## 7. Distribution — one package, one-shot everywhere

```
uvx voice-bridge …                                                       (on PyPI)
uvx --from git+https://github.com/YoraiLevi/ai-voice-reminders-bridge voice-bridge …   (GitHub, no clone)
uv run voice-bridge …                                                    (local checkout)
```

`pyproject.toml` with `console_scripts: voice-bridge = voice_bridge.cli:main`.

## 8. Package layout

```
voice_bridge/
  cli.py            argparse subcommands → the interface
  config.py         resolution + fields + get/set  (foundation, 0 internal deps)
  mailbox.py        line format · dedupe/seen · to-manager/to-vox I/O · join/eject
  ntfy.py           the one banner sender
  transport.py      Transport ABC + shared poll/drain loop + FakeTransport-testable seams
  icloud.py         ICloudTransport (pyicloud)
  caldav.py         CalDAVTransport (+ CalDAV plumbing)
  bootstrap.py      setup/provision + the run state machine helpers
  prompts/vox.md    the phone prompt template
pyproject.toml
tests/  unit · contract(FakeTransport) · integration(embedded Radicale) · e2e(subprocess)
```

Transport ABC (the ~5 ops that actually differ):
```
class Transport(ABC):
    def connect(self) -> Session
    def list_todo_lists(self) -> list[ListRef]          # name + id  (powers `lists`)
    def resolve_list(self, name, id) -> ListHandle       # ghost-safe via id
    def read_incomplete(self, list) -> list[Item]
    def add_todo(self, list, summary, body) -> str
    def complete(self, item) -> None
```
Everything else (mailbox contract, dedupe, ntfy, poll/drain, CLI) is shared and transport-agnostic.

## 9. Test harness (build tests FIRST)

Every capability tested against a controllable oracle; the network is touched only where an
embeddable oracle exists (Radicale) — never Apple.

| layer | dir | oracle |
|---|---|---|
| unit (pure) | `tests/unit` | none — config, mailbox line, dedupe, clips, `--set`, url-extract, ntfy-build |
| contract | `tests/contract` | **FakeTransport** — Transport ABC obeyed; full poll→drain→reply cycle; `run` state machine |
| integration | `tests/integration` | **embedded Radicale** — real list create/add/complete/discover; setup provision |
| e2e | `tests/e2e` | subprocess `voice-bridge …` — show-config, dry-run, once, provision idempotency |

Key fixtures: `tmp_mailbox`, `sample_config`, `fixed_clock`, **`fake_transport`**,
`radicale_server` (embedded), `fake_ntfy` (localhost HTTP).

Apple is deliberately un-integration-tested (2FA + private API can't live in CI); the Transport
ABC + FakeTransport give the iCloud path behavioral coverage anyway.

Build order: (1) pyproject + `FakeTransport` + `fixed_clock`; (2) unit tests against ported pure
functions (green baseline that pins current behavior); (3) `run` state-machine contract tests on
FakeTransport; (4) embedded-Radicale integration; (5) port the two real transports behind the ABC
last, keeping everything green.

## 10. Open items / not-yet-decided

- Multi-line replies: start line-per-message (protocol-native); add a block delimiter only if needed.
- `state_dir` on Windows: `~/.local/state/vox-mailbox` works via pathlib; consider `platformdirs`
  for `%LOCALAPPDATA%` correctness later.
- Mesh presence: not ours until the mailbox protocol exposes liveness; keep `route_to` config-driven.
