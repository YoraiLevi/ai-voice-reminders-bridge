# Example 1 — SINGLE project, Radicale (RECOMMENDED baseline)

One project, driven by voice, onboarded with **one command** and **zero manual steps**:

```
claude @SETUP-RADICALE.md
```

No Apple account, no 2FA, and no "go make two lists on the phone" step. A CalDAV client is allowed to
*create* collections on a self-hosted Radicale server, so the setup provisions the two lists itself and
they sync down to the phone's Reminders automatically.

The shipped config lives at [`myapp/.claude/voice-bridge.json`](myapp/.claude/voice-bridge.json) — a
**Radicale** config whose `creds_env` points at `~/.auth/radicale.env` (server URL + Radicale
user/pass), so the poller talks to Radicale, not iCloud.

---

## The one command

1. Copy **`SETUP-RADICALE.md`** (repo root) into your project folder (here, `myapp/`).
2. In that folder, run **`claude @SETUP-RADICALE.md`**.

Same file, same command for the **first launch** and **every relaunch** — the only branch is whether
`.claude/voice-bridge.json` already exists.

---

## EXACTLY what happens hands-off (first launch)

The Claude Code agent does all of this for you, in order:

1. **DETECT.** No `./.claude/voice-bridge.json` yet → first launch → provision.
2. **Locate the voice-bridge repo once** (checks `~/source/voice-bridge`, else a bounded search for the
   dir holding both `radicale_bootstrap.py` and `reminder_bridge.py`); records it as `vb_path` so
   nothing searches again.
3. **Run `uv run <vb>/radicale_bootstrap.py`** from `myapp/`. In ONE idempotent step it:
   - derives the distinct list names from the folder name → **`To Myapp`** / **`From Myapp`**;
   - writes `.claude/voice-bridge.json` with **`creds_env` → `~/.auth/radicale.env`** and `vb_path`;
   - **connects to Radicale over CalDAV and CREATES the two VTODO lists** if missing (`make_calendar`,
     guarded by an existence check). It **refuses to run against iCloud** — creating lists is the point.
   - prints `PROVISIONED: myapp` (or `ALREADY PROVISIONED: myapp` on a re-run) + the start command.
4. **NO phone step.** The two lists now exist on Radicale; because the phone already has the ONE shared
   Radicale CalDAV account added, `To Myapp` / `From Myapp` appear in Reminders **automatically**.
5. **START the poller in the background** and listen:
   ```
   uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json
   ```
   (`reminder_bridge.py` is the CalDAV poller; `pyicloud_bridge.py` is the iCloud one.)
6. **Arm a Monitor** on `~/.claude/message-protocol/myapp/to-manager.md` so a new inbound line wakes
   the session.

**Every relaunch** skips steps 2-4: the config exists, so the agent reads `vb_path` and goes straight
to steps 5-6.

> One-time **server** setup (once, for ALL Radicale projects — not per project): run the Radicale
> server, seed `~/.auth/radicale.env`, add the one CalDAV account to the phone.
> Detail: [`../../radicale/OWNER-SETUP.md`](../../radicale/OWNER-SETUP.md).

---

## The operating contract (what the running session does)

Each new inbound line looks like `- [HH:MM] (myapp-phone) <text>` — a request from the phone. Do the
work, then **reply through the bridge so the ntfy banner fires** — never write a raw CalDAV todo
yourself (it would surface no notification):

- **CLI:** `uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"`
- **SHORT single line:** `printf '%s\n' 'your message' >> ~/.claude/message-protocol/myapp/to-phone.md`
  (the running poller drains it within one interval).

Both paths route through `reminder_bridge.send_reply(cfg, text, notify=True)`, which creates the
`From Myapp` VTODO **and** pushes the ntfy banner. Long CONTENT (a design, a doc) →
`<vb_path>/deliver_content.sh <file> "one-line summary"` (gist + tappable link).

Standard rules from `SETUP-RADICALE.md` carry over: async/turn-based, newest supersedes, clarify
misheard voice terms before acting, keep replies complete and self-contained.
