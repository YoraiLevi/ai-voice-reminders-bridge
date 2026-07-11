# Example 4 — MULTI project, Apple / iCloud (works, but NOT recommended)

Two projects — **gamma** and **delta** — through one phone assistant, on Apple/iCloud. This works, but
it is the **least recommended** of the four quadrants, for one concrete reason spelled out below:
**several iCloud pollers on one shared Apple ID can trigger an auth storm.** If you run more than one
project by voice, prefer [Example 3 (multi + Radicale)](../multi-radicale/WALKTHROUGH.md).

```
# in the gamma/ folder:
claude @SETUP.md
# in a SEPARATE session, in the delta/ folder:
claude @SETUP.md
```

Shipped configs (both **iCloud**, `creds_env` → `~/.auth/icloud.env`):
- [`gamma/.claude/voice-bridge.json`](gamma/.claude/voice-bridge.json) → `To Gamma` / `From Gamma`
- [`delta/.claude/voice-bridge.json`](delta/.claude/voice-bridge.json) → `To Delta` / `From Delta`

---

## ⚠ The shared-Apple-account danger (today's real incident)

All iCloud projects share **one** Apple login under `~/.auth`. Apple **throttles** an Apple ID that
sees too many rapid authenticated requests and will **force a fresh 2FA challenge** when it does. We
hit exactly this today: **multiple iCloud pollers auto-re-authing against ONE Apple ID at the same
time** produced an **auth storm — Apple 503 throttling plus a forced re-2FA** — which stalls *every*
project on that account until you re-seed the session (`pyicloud_login.py`).

If you insist on multi-project iCloud, hold this discipline:
- **Strictly one poller per Apple account.** Do not point two fast pollers at the same Apple ID. If you
  must run several projects on iCloud, give each its **own** Apple ID (`creds_env` per project) — the
  configs already have a distinct `creds_env` slot to make that possible.
- Keep `poll_interval` sane (default 10s) and do not restart pollers in a tight loop (each restart
  re-auths).
- The moment you see repeated `503` / "session needs 2FA", **stop the extra pollers** before re-seeding
  — a swarm re-authing during recovery just re-triggers the throttle.

Radicale has **no such limit** — which is why Example 3 is the recommended multi-project path.

---

## What is distinct per project — and WHY

Same distinct-fields rule as the Radicale multi example (each poller must read only its own list and
mailbox):

| field | gamma | delta | why distinct |
|-------|-------|-------|--------------|
| `inbox_list` | `To Gamma` | `To Delta` | each poller reads ONLY its own inbox list |
| `output_list` | `From Gamma` | `From Delta` | replies surface on their own list |
| `mailbox_dir` | `~/.claude/message-protocol/gamma` | `~/.claude/message-protocol/delta` | distinct dirs keep the two managers from reading each other's `to-manager.md` |

---

## EXACTLY what happens, per project (first launch)

For each of `gamma/` and `delta/`, `claude @SETUP.md` does:

1. **DETECT** no config → set up.
2. **Locate the repo once** → record `vb_path`.
3. **`uv run <vb>/bootstrap.py`** → derives `To Gamma`/`From Gamma` (resp. `To Delta`/`From Delta`),
   writes the config (`creds_env` → `~/.auth/icloud.env`), prints
   `SETUP_DONE lists_needed=To Gamma|From Gamma`. **No network, no list creation.**
4. **THE MANUAL STEP — once PER PROJECT.** iCloud can't create lists, so the agent tells you to make
   **two Reminders lists by hand** named EXACTLY `To Gamma` / `From Gamma` (and, for the other project,
   `To Delta` / `From Delta`) and waits for you to confirm. That is **two manual list-creations per
   project** — four lists total for this example.
5. **START** `uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json` in the
   background, and **Monitor** that project's `to-manager.md`.

Run the two projects in **two separate Claude Code sessions**. Distinct list names + `mailbox_dir` keep
them from crossing wires (the mailbox collision, not the account, is what distinctness protects against;
the account throttle is the separate danger above).

---

## The phone side — ONE assistant routes to BOTH projects

Save [`../PHONE-ASSISTANT-PROMPT.md`](../PHONE-ASSISTANT-PROMPT.md) in the phone's Claude app and set
its ROUTING TABLE to this example's projects:

```
DEFAULT PROJECT: gamma

PROJECT: gamma
  TO   list (I send here):     "To Gamma"
  FROM list (you read here):   "From Gamma"

PROJECT: delta
  TO   list (I send here):     "To Delta"
  FROM list (you read here):   "From Delta"
```

*"on delta, run the tests"* → assistant drops it into `To Delta`; delta's poller picks it up. Every
turn it reads both `From Gamma` and `From Delta` and reads replies back naming the project.

---

## The operating contract (both projects)

Reply through the bridge so the ntfy banner fires (both write into the output list via
`pyicloud_bridge.send_reply(cfg, text, notify=True)`):

- **SHORT single line:** `printf '%s\n' 'your message' >> ~/.claude/message-protocol/gamma/to-phone.md`
- **LONGER / multi-line:** `uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"`

Long CONTENT → `<vb_path>/deliver_content.sh <file> "summary"`. Standard rules from `SETUP.md` carry
over. If the poller prints **"session needs 2FA"**, re-run `pyicloud_login.py` once (and mind the
auth-storm rule above if several pollers are running).
