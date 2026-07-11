# Example 3 — MULTI project, Radicale (RECOMMENDED for multi-project)

Two projects — **alpha** and **beta** — driven by voice through **one** phone assistant, each
onboarded with the same one command and **zero manual steps**. This is the recommended way to run
several projects at once: it is fully self-provisioning (Claude creates every list for you) and
**throttle-free** (Radicale has no per-account rate limit).

```
# in the alpha/ folder:
claude @SETUP-RADICALE.md
# in a SEPARATE session, in the beta/ folder:
claude @SETUP-RADICALE.md
```

Both projects live on **ONE Radicale server** reached through **ONE CalDAV account** already added to
the phone. Only the per-project **list names** and **`mailbox_dir`** differ.

Shipped configs (both **Radicale**, `creds_env` → `~/.auth/radicale.env`):
- [`alpha/.claude/voice-bridge.json`](alpha/.claude/voice-bridge.json) → `To Alpha` / `From Alpha`
- [`beta/.claude/voice-bridge.json`](beta/.claude/voice-bridge.json) → `To Beta` / `From Beta`

---

## What is distinct per project — and WHY it must be

Two pollers run side by side on one PC. For them **never to cross wires**, three fields are distinct
per project (both configs already are):

| field | alpha | beta | why distinct |
|-------|-------|------|--------------|
| `inbox_list` | `To Alpha` | `To Beta` | each poller reads ONLY its own inbox list, so a phone request lands with exactly one project |
| `output_list` | `From Alpha` | `From Beta` | replies surface on their own list, so the phone can say which project answered |
| `mailbox_dir` | `~/.claude/message-protocol/alpha` | `~/.claude/message-protocol/beta` | the file mailbox is **not** namespaced — distinct dirs keep the two managers from reading each other's `to-manager.md` |

`name` / `from_name` are distinct too (`alpha` / `alpha-phone`, …) so the dedupe seen-files never
collide and each inbound line is tagged with which project's phone it came from.

**Shared, safely:** `creds_env`, `cookie_dir`, `ntfy_topic_file` all point at the same `~/.auth`
files — one Radicale account serves every project's poller.

---

## EXACTLY what happens hands-off, per project (first launch)

For each of `alpha/` and `beta/`, `claude @SETUP-RADICALE.md` does:

1. **DETECT** no config → provision.
2. **Locate the repo once** → record `vb_path`.
3. **`uv run <vb>/radicale_bootstrap.py`** → derives `To Alpha`/`From Alpha` (resp. `To Beta`/`From
   Beta`) from the folder name, writes the config (`creds_env` → `~/.auth/radicale.env`), and
   **connects to Radicale and CREATES the two lists** if missing. Prints `PROVISIONED: alpha`.
4. **NO phone step** — the new lists sync to the phone's Reminders automatically via the one shared
   CalDAV account.
5. **START** `uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json` in the
   background, and **Monitor** that project's `to-manager.md`.

Run these in **two separate Claude Code sessions** (one per folder). Because the list names and
`mailbox_dir` are distinct, the two pollers and two managers never touch each other's mail.

---

## The phone side — ONE assistant routes to BOTH projects

Save [`../PHONE-ASSISTANT-PROMPT.md`](../PHONE-ASSISTANT-PROMPT.md) in the phone's Claude app. Its
**ROUTING TABLE** maps each project to its list pair (this example's is Alpha/Beta):

```
DEFAULT PROJECT: alpha

PROJECT: alpha
  TO   list (I send here):     "To Alpha"
  FROM list (you read here):   "From Alpha"

PROJECT: beta
  TO   list (I send here):     "To Beta"
  FROM list (you read here):   "From Beta"
```

Then: *"on alpha, add a login button"* → the assistant drops it into `To Alpha`; alpha's poller picks
it up; that manager does the work and replies into `From Alpha`. Every turn the assistant silently
reads **both** `From Alpha` and `From Beta` and reads any reply back **saying which project it is
from**. To add a project = copy one `PROJECT:` block; to roll back to one project = keep only the
DEFAULT block.

---

## The operating contract (both projects)

Reply **through the bridge so the ntfy banner fires** — never write a raw CalDAV todo:

- **CLI:** `uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"`
- **SHORT single line:** `printf '%s\n' 'your message' >> ~/.claude/message-protocol/alpha/to-phone.md`

Both route through `reminder_bridge.send_reply(cfg, text, notify=True)` (creates the output-list VTODO
**and** pushes ntfy). Replies are auto-stamped `[HH:MM][alpha]` so the phone can tell projects apart.
Long CONTENT → `<vb_path>/deliver_content.sh <file> "summary"`. Standard rules from `SETUP-RADICALE.md`
carry over.

**Why this is the recommended multi-project path:** every list is created for you, there is no manual
phone step, and Radicale has **no per-account throttle** — so any number of pollers can run at once.
The iCloud multi-project variant ([Example 4](../multi-icloud/WALKTHROUGH.md)) works but carries a real
auth-storm risk on the shared Apple ID.
