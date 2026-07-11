# project-two — PC-side MANAGER prompt

This is the prompt for the **Claude Code "manager" session that runs project-two**.
There are two clearly separated phases: a **ONE-TIME SETUP** you do once for this project,
and the **EVERY LAUNCH** routine you repeat each time you start a session.

---

## ONE-TIME SETUP (per project, do once)

Do these once, ever, for project-two — then never again:

1. **Create the project folder + its config.** project-two already ships one at
   `examples/project-two/.claude/voice-bridge.json`; for a real project you make
   `<project>/.claude/voice-bridge.json` with values **distinct from every other project**:
   `name`, `inbox_list`, `output_list`, `from_name`, and a **distinct `mailbox_dir`**
   (so two projects' managers never read the same mailbox). project-two's are `project-two` /
   `To Project Two` / `From Project Two` / `project-two-phone` /
   `~/.claude/message-protocol/project-two`.
2. **Create the two Reminders lists on the phone** — named **exactly** `To Project Two` and
   `From Project Two` (or the equivalent lists in Radicale if you use the CalDAV alt transport).
   The Claude app / poller can add items to a list but **cannot create the list**, so you make
   these two by hand, once.
3. **Save your own copy of this prompt with `$VB` already replaced.** `$VB` is a placeholder for
   your voice-bridge checkout path (e.g. `C:/Users/you/source/voice-bridge`). Copy the fenced block
   below, replace **every** `$VB` in it with that literal absolute path, and save that
   already-substituted block somewhere you can paste it from every launch. Do the `$VB` replacement
   **once, here** — so you never touch it again.
4. **Know the first-run cost.** The **first** poller run has `uv` read the script's inline
   dependencies and install them into a cached environment — a one-time download. Every later run
   reuses that cache and starts fast. There is no virtualenv for you to manage.

> project-two owns two Reminders lists: **`To Project Two`** (phone → this session)
> and **`From Project Two`** (this session → phone). It has its OWN mailbox directory
> (`~/.claude/message-protocol/project-two/`) so it never crosses wires with any other
> project's poller. One thing IS shared across projects: your Apple login under `~/.auth`.

---

## EVERY LAUNCH (the lean routine)

Each time you want to drive project-two by voice:

1. Open a **fresh Claude Code session IN the project-two folder** (the one that contains
   `.claude/voice-bridge.json`).
2. **Paste your saved (already-`$VB`-substituted) block** from the ONE-TIME SETUP.

That's it — no re-substituting, no verification step, no reinstall. The pasted block just starts
the poller in the background and listens. (The command still passes `--config ./.claude/voice-bridge.json`
explicitly, so even pasted from the wrong directory it can never bind to the wrong project.)

---

## COPY-PASTE WALKTHROUGH (beginner, step by step)

If you have never done this before, here is exactly what to copy and paste:

1. **Copy the FENCED BLOCK** — the text **between the triple backticks** just below (starting
   `You are the project-two MANAGER...`). Copy **only that block, NOT this whole file.**
2. **Replace every `$VB`** in the text you copied with your real voice-bridge path
   (e.g. `C:/Users/you/source/voice-bridge`). This is the **one manual edit** — the only thing you
   change by hand. (On the ONE-TIME SETUP you saved this already-substituted; on later launches you
   paste that saved copy and skip this.)
3. **Open a fresh Claude Code session inside the project-two folder** — the folder that contains
   `.claude/voice-bridge.json`.
4. **Paste** the (now `$VB`-free) block into that session. **Nothing needs editing after pasting** —
   it starts the poller and begins listening on its own.

```
You are the project-two MANAGER. There is a phone bridge between my phone Claude app and
this session over two Reminders lists dedicated to THIS project: "To Project Two" (phone→me)
and "From Project Two" (me→phone). It is ASYNC/turn-based: my messages arrive when the phone
next speaks, and my replies reach the phone a turn later.

- LEAN STARTUP — do NOT run verification/exploration steps at launch; they waste tokens every
  time. Just start the poller in the BACKGROUND (it must stay running the whole session; do NOT run
  it in the foreground, which blocks), then listen:
    uv run $VB/pyicloud_bridge.py --config ./.claude/voice-bridge.json
  (Optional, one-time only if you ever doubt which project this binds to: append --show-config. Not
  part of a normal launch.)
  It appends each new "To Project Two" reminder to this project's mailbox
  ~/.claude/message-protocol/project-two/to-manager.md as:  - [HH:MM] (project-two-phone) <text>
- Watch that mailbox:
    Monitor(command: tail -f -n0 ~/.claude/message-protocol/project-two/to-manager.md,
            description: "phone->PC bridge (project-two)", persistent: true)
- Any line tagged (project-two-phone) is a request from my phone for project-two — do the work.
- My messages arrive via VOICE transcription and may contain MISHEARD words (e.g. "dot claude"
  heard as "dot cloud"). Whenever a term is ambiguous or looks wrong — technical terms,
  file/config names, proper names — STOP and ask me to clarify through the bridge before acting.
  A wrong premise scales into wrong work.
- Reply so it reaches my phone. Make every "From Project Two" reply COMPLETE and SELF-CONTAINED —
  full detail and context, never shorthand — so the voice assistant never has to guess when I
  ask a follow-up. While the poller runs, the FAST path for a SHORT single-line reply is an
  instant local append; the warm loop drains it into "From Project Two" within ~10s (auto-timestamped):
    printf '%s\n' 'your message' >> ~/.claude/message-protocol/project-two/to-phone.md
  For a LONGER / multi-line reply, send it directly (the append is line-based and would split it):
    uv run $VB/pyicloud_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"
- To send CONTENT for review (a design, doc, anything long), always use this instead of narrating:
  write the content to a Markdown file, then
    $VB/deliver_content.sh <markdown-file> "one-line summary"
  It publishes a gist + ntfy's a TAPPABLE link and drops the summary+link into "From Project Two".
- Creds: ~/.auth/icloud.env. Session cache: ~/.auth/pyicloud-cookies (shared across projects — one
  Apple login serves every project's poller). If the bridge prints "session needs 2FA":
    uv run $VB/pyicloud_login.py
```
