# REFACTOR PLAN — this repo is an INTERFACE to the mesh, not the mesh itself

> Tracking: **issue #4** (this refactor) · depends on **issue #3** (the mesh design /
> the `join/send/poll/ack` contract). Discussion is issues-first; this doc is the
> companion PR's starting point, deliberately **conservative** — it does the safe
> structural framing now and defers anything that would couple to #3's un-settled
> contract or risk the **live, running** Reminders/Vox bridge.

## The point in one paragraph

Right now this repo **is** the mesh: the phone-facing iCloud/Vox bridge, the
reliability core (dedup / mark-complete / cursor / watchdog), multi-agent
addressing (the Vox routing table), and the Radicale substrate are all fused into
the same flat set of modules. Issue #4 says narrow this repo to **one interface
implementation** — the Reminders / Vox phone-facing adapter — that *plugs into* a
separate, decoupled **mesh**. The mesh (Radicale substrate, addressing/routing/
directory, the reliability core, cross-project messaging) becomes an underlying
system this repo *talks to*, not its home.

This is the ports-and-adapters framing from #3: the human-gateway / Reminders
interface is **one adapter**; the mesh core is separate and reusable by any agent.

---

## 1. Concern classification (every tracked module + doc)

Legend — **INTERFACE** = the iCloud Reminders / Vox phone-facing adapter (stays
here, is the whole point of this repo going forward) · **MESH** = substrate /
addressing / reliability core / cross-agent (extract to the mesh) · **SHARED** =
config / CalDAV plumbing / utilities (a thin shared layer both sides use).

| File / dir | Concern | What it does | Refactor disposition |
|---|---|---|---|
| `pyicloud_bridge.py` | **INTERFACE + MESH (fused)** | iCloud Reminders poll/reply/drain over pyicloud — the phone-facing bridge. But the poll loop also embeds the **reliability core** (seen-file dedup, `_mark_complete`, retire-at-read). | Keep the phone I/O + render (`send_reply`, `_notify_push`, `format_mailbox_line`, `drain_replies`). **Extract** the dedup/mark-complete/loop into the mesh reliability core; call it via the seam. The ack-at-read bug (#3 §1) is fixed *there*, not here. |
| `reminder_bridge.py` | **INTERFACE + MESH (fused)** | Same contract over CalDAV/Radicale — the manager's fallback channel **and** the native-alarm path (VALARM). Same embedded reliability core as above. | Keep the alarm/render/reply as an interface concern (native alarm is a *phone-notification* behavior). **Extract** the identical reliability core. Note: worker channels that also ride this transport are a **mesh** concern, not interface. |
| `vox_instructions.py` | **INTERFACE + MESH (fused)** | Renders Vox's GLOBAL RULES / BOOTSTRAP / LIVE STATE onto the phone (**interface**) **and** owns the multi-project **ROUTING TABLE** with CAS upsert (**mesh addressing/directory**). | Keep the phone-side prompt/rules/live-state rendering. **Extract** the routing table into the mesh **directory**; the interface then renders a *view* of `mesh.directory()`, it does not author the roster. |
| `deliver_content.sh` | **INTERFACE** | gist + ntfy tappable banner + output-list spoken walkthrough — content delivery to the phone. | Stays. Pure interface/notification behavior. |
| `pyicloud_login.py` | **INTERFACE** | One-time Apple 2FA login → cached trusted session. Apple creds = the **human fast lane** (the one dual-creds crossing in #3). | Stays. Apple-credential handling is intrinsic to *this* interface; the mesh never holds Apple creds. |
| `probe.py` | **SHARED (diagnostic)** | GO/NO-GO CalDAV feasibility probe (read-only). | Stays as a shared diagnostic; leans mesh-substrate (it tests the CalDAV server), but harmless where it is. |
| `bootstrap.py` | **SHARED (provisioning)** | Writes a project's `voice-bridge.json` (no network). | Stays; it configures the *interface* side. The list-name half is interface; the creds/transport half is shared. |
| `radicale_bootstrap.py` | **MESH (provisioning)** | Self-provisions a project onto Radicale: creates collections, seeds them, points config at Radicale, prints the Vox-routing registration step. | **Extract** to the mesh. Creating collections / users / rights is substrate provisioning. The bit that writes the interface config can stay behind. |
| `radicale/run_radicale.py` | **MESH (substrate)** | Launches the Radicale CalDAV server. | **Extract** to the mesh. This is the substrate itself. |
| `radicale/make_user.py` | **MESH (substrate)** | Creates the Radicale htpasswd user (bcrypt). Becomes per-agent creds in #3. | **Extract** to the mesh (isolation/identity provisioning). |
| `radicale/init_lists.py` | **MESH (substrate)** | Creates the two VTODO collections server-side. | **Extract** to the mesh. |
| `radicale/config`, `radicale/OWNER-SETUP.md` | **MESH (substrate)** | Radicale server config + its owner setup. | **Extract** to the mesh. |
| `config.py` | **SHARED** | Per-project config resolution + `~` expansion. Mixes **interface** fields (`inbox_list`, `output_list`, `from_name`, `ntfy_topic_file`) with **transport/mesh** fields (`creds_env`, `cookie_dir`, poll cadence). | Stays as the shared layer. Later: split the schema into an `interface:` block and a `mesh:` block so the seam is visible in config too. |
| `_caldav.py` | **SHARED** | CalDAV connect / creds / list discovery. Used by the interface alt-transport **and** the mesh substrate scripts. | Stays shared; most of it migrates toward the mesh client (`mesh.py`) as the reliability core moves. |
| `mesh_seam.py` | **SHARED (new, this PR)** | The **PROVISIONAL, un-wired** `Mesh` Protocol naming the boundary the adapter will call through (`join/send/poll/ack/directory`). Imported by nothing. | The target of the whole refactor. Replaced by a real `mesh.py` once #3 settles. |
| `docs/ARCHITECTURE.md`, `docs/BRIDGE-INSTRUCTIONS.md`, `examples/PHONE-ASSISTANT-PROMPT.md`, `docs/OWNER-SETUP.md`, `docs/ONBOARDING.md`, `docs/TROUBLESHOOTING.md` | **INTERFACE (docs)** | Phone-side + PC-side prompts, owner setup, the bridge's own architecture/onboarding. | Stay — they document the interface. |
| `docs/RELIABILITY-DESIGN.md`, `docs/DEEP-DIVE.md`, `docs/RADICALE-AUTOSTART.md` | **MESH (docs)** | Reliability core + Radicale substrate deep material. | Move with the mesh extraction (these describe mesh concerns). |
| `examples/multi-radicale/`, `examples/multi-icloud/` | **SHARED (examples)** | Multi-project example configs. | Split per the interface/mesh config schema when that lands. |

**The one structural fact that drives the whole plan:** every module imports
`config` and `_caldav` as **flat repo-root siblings**, and several use
`sys.path.insert(REPO_DIR)` / `import pyicloud_bridge` by name. So *moving any file
into a package directory breaks every `uv run …` entrypoint the owner launches*.
That is why the directory split is a **later, reviewed phase**, not this PR.

---

## 2. Target architecture

```
        PHONE (Vox / iCloud Reminders / ntfy)
                  │  render / translate
                  ▼
   ┌───────────────────────────────────┐        this repo, after the refactor:
   │  REMINDERS INTERFACE ADAPTER       │        ONE interface implementation.
   │  (this repo)                       │        - phone I/O: poll/reply/drain
   │   - iCloud Reminders + ntfy + alarm│        - render MeshMessage <-> phone line
   │   - Vox rules/bootstrap/live-state │        - Apple creds (the human fast lane)
   │   - renders directory() as routing │        - joins the mesh as ONE agent
   └───────────────┬───────────────────┘          (e.g. `human-gw` in #3)
                   │  mesh_seam.Mesh  (join/send/poll/ack/directory)
                   ▼    ── the boundary this PR names ──
   ┌───────────────────────────────────┐        SEPARATE, decoupled system (#3):
   │  MESH  (a dependency, not here)    │        - Radicale substrate + provisioning
   │   - reliability core (ack/dedup/   │        - addressing / routing / directory
   │     cursor/watchdog, per-inbox)    │        - the reliability core
   │   - isolation (per-agent creds,    │        - cross-project / multi-agent
   │     rights, signed envelopes)      │          messaging
   └───────────────────────────────────┘        reusable by ANY agent, not phone-specific
```

The adapter depends on the **mesh abstraction** (`mesh_seam.Mesh` → later
`mesh.py`), never on CalDAV/pyicloud reliability details directly. The mesh knows
nothing about phones, Reminders, ntfy, or Vox.

---

## 3. The interface seam (what the adapter calls)

`mesh_seam.py` (added in this PR) defines the boundary as a `typing.Protocol`:

- `join(agent_id)` — the adapter joins the mesh as one agent; substrate
  provisioning is the mesh's job.
- `send(to, body, kind, reply_to) -> id` — phone request → mesh message.
- `poll() -> [MeshMessage]` — verified, deduped, not-yet-processed messages.
- `ack(message) -> Receipt` — **process-then-ack**, the fix for the retire-at-read
  bug; called only *after* the phone-facing delivery is done.
- `directory() -> {agent_id: entry}` — the roster the interface renders as Vox's
  routing table.

It is **PROVISIONAL and imported by nothing** — a design artifact so the boundary
can be reviewed before code moves. It mirrors #3 §5's proposed contract but is not
frozen; #3 owns the final shape.

---

## 4. Phased migration

**Phase 0 — framing (THIS PR).** Add `mesh_seam.py` (un-wired) + this plan +
the classification table. **No logic changes, no file moves, no import changes.**
The live pollers are byte-for-byte untouched. Purpose: agree the boundary and the
dispositions in review, in parallel with #3.

**Phase 1 — mesh API frozen (BLOCKED on #3).** When #3 settles `join/send/poll/
ack/directory` and the isolation topology (A1 vs A2), replace `mesh_seam.py`'s
Protocol with the agreed signatures.

**Phase 2 — extract the reliability core.** Lift the duplicated dedup /
mark-complete / poll-loop out of `pyicloud_bridge.py` **and** `reminder_bridge.py`
into the mesh client (`mesh.py`), implementing process-then-ack + dedup-by-id +
durable cursor + watchdog there (this is where #3 §1's live bug is actually
fixed). The bridges keep only phone I/O + render and call the seam.

**Phase 3 — extract the substrate.** Move `radicale/`, `radicale_bootstrap.py`,
and the mesh docs into the mesh (its own repo or package). This repo stops
shipping the server.

**Phase 4 — extract addressing.** Move the routing table out of
`vox_instructions.py` into the mesh directory; the interface renders
`directory()` as a Vox view.

**Phase 5 — directory reorg + config split.** Only once entrypoints are updated
in lockstep: group the remaining interface modules, split `config.py`'s schema
into `interface:` / `mesh:` blocks, update every `uv run …` path and the launcher.

Phases 2–5 each land as their own reviewed PR against `sync`; none merges without
the running bridge staying green.

---

## 5. What WAITS on #3 (explicit)

Nothing below is done in this PR — each is blocked on #3's contract and would be
premature coupling if guessed now:

- **The exact `mesh.py` signatures** (`join/send/poll/ack/directory`) and the
  envelope schema (§5/§4 of #3) — the seam here is a placeholder.
- **The isolation topology** — A1 shared-inbox vs A2 sender-partitioned (#3 §10,
  gated on the peer-delete test). Determines how `send`/`ack` address inboxes.
- **Signing** — ed25519 from v1 or deferred (#3 §11 Q2).
- **Checker shape** — always-on auditor/watchdog vs one cron reconcile (#3 §11 Q3).
- **Where the durable cursor/ledger/outbox live** (#3 §11 Q7) — a PITFALLS
  double-sync hazard; a mesh-side decision.
- **`human-gw` = manager or separate** (#3 §11 Q9) — which agent this adapter
  actually is.

Until those settle, the adapter keeps its **current** transports and behavior
verbatim; only the *seam and the plan* exist.

---

## 6. Safety envelope for this PR

- Built in an **isolated worktree** on `refactor/interface-vs-mesh` off `sync`;
  the live bridge runs from a **different** working tree and is never touched.
- **Additive-only**: one new module (`mesh_seam.py`, imported by nothing) + two
  docs. Zero edits to any running module. Every `uv run …` entrypoint and every
  cross-module import resolves exactly as before.
- Opens as a **PR against `sync`** (branch-protected: PR + 1 approval,
  enforce_admins) and **does not merge** — it is a discussion starting point,
  reviewed alongside #3.

_Refs #4; depends on #3._
