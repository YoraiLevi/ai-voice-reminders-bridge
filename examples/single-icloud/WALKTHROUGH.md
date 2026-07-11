# Example 2 — SINGLE project, Apple / iCloud (fallback)

The same single-project setup as [Example 1](../single-radicale/WALKTHROUGH.md), but on Apple/iCloud
instead of Radicale. Use this only if you'd rather not run a Radicale server. One command:

```
claude @SETUP.md
```

It is the same "one file, one command" shape, with **one unavoidable difference**: iCloud does **not**
let a client create a Reminders list, so `bootstrap.py` self-configures everything *except* the two
lists — **you must create those two lists on the phone by hand**. That single manual step is exactly
what Radicale removes.

The shipped config lives at [`myapp/.claude/voice-bridge.json`](myapp/.claude/voice-bridge.json) — an
**iCloud** config whose `creds_env` points at `~/.auth/icloud.env` (your Apple ID + password).

---

## The one command

1. Copy **`SETUP.md`** (repo root) into your project folder (here, `myapp/`).
2. In that folder, run **`claude @SETUP.md`**.

Same file, same command for first launch and every relaunch.

---

## EXACTLY what happens (first launch)

1. **DETECT.** No `./.claude/voice-bridge.json` yet → first launch → set up.
2. **Locate the voice-bridge repo once** (checks `~/source/voice-bridge`, else a bounded search for the
   dir holding both `bootstrap.py` and `pyicloud_bridge.py`); records it as `vb_path`.
3. **Run `uv run <vb>/bootstrap.py`** from `myapp/`. It:
   - derives the distinct list names from the folder name → **`To Myapp`** / **`From Myapp`**;
   - writes `.claude/voice-bridge.json` with **`creds_env` → `~/.auth/icloud.env`** and `vb_path`;
   - prints `SETUP_DONE lists_needed=To Myapp|From Myapp`.
   - **It does NOT touch the network, your Apple credentials, or create any list** — iCloud/pyicloud
     *cannot* create Reminders lists, so bootstrap only writes the local config.
4. **THE ONE MANUAL STEP (this is the difference).** The agent tells you, in plain words, to open the
   phone's **Reminders** app and create **two lists named EXACTLY** `To Myapp` and `From Myapp`, and
   **waits for you to confirm** they exist before continuing.
5. **START the poller in the background** and listen:
   ```
   uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json
   ```
6. **Arm a Monitor** on `~/.claude/message-protocol/myapp/to-manager.md`.

**Every relaunch** skips steps 2-4.

> One-time **Apple-account** setup (once, for ALL iCloud projects): create `~/.auth/icloud.env` (Apple
> ID + password), seed the trusted session once with a 6-digit 2FA code
> (`uv run <vb>/pyicloud_login.py`, good ~60 days), set `~/.auth/ntfy-topic.txt`.
> Detail: [`../../docs/OWNER-SETUP.md`](../../docs/OWNER-SETUP.md). If the poller ever prints
> **"session needs 2FA"**, re-run `pyicloud_login.py` once.

---

## The operating contract (what the running session does)

Each new inbound line looks like `- [HH:MM] (myapp-phone) <text>`. Do the work, then reply (both paths
write into `From Myapp`, and both fire the ntfy banner via `send_reply`):

- **SHORT single line:** `printf '%s\n' 'your message' >> ~/.claude/message-protocol/myapp/to-phone.md`
- **LONGER / multi-line:** `uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"`

Long CONTENT → `<vb_path>/deliver_content.sh <file> "one-line summary"`. Standard rules from `SETUP.md`
carry over: async/turn-based, newest supersedes, clarify misheard voice terms, complete replies.

**Single project is throttle-safe on iCloud.** One poller against one Apple ID stays well within
limits. The throttle risk only appears when you run *several* iCloud pollers on one account — see
[Example 4](../multi-icloud/WALKTHROUGH.md).
