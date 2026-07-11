# Onboarding — talk to your PC's Claude Code from your phone, across many projects

This is the from-zero, plain-English walkthrough. It assumes **no prior context**. By the end
you will be able to say something to your phone like *"on project one, add a login button"* and
have a Claude Code session on your PC actually do it — and hear its reply back on your phone —
for as many projects as you like, all through **one** voice assistant.

We use the two shipped example projects (`project-one`, `project-two`) as the running example.

---

## 1. The one-paragraph mental model

You talk to the **Claude app on your phone**. It cannot reach your PC directly. So we use
**Apple Reminders lists as a shared mailbox**: the phone drops a task into a list; a little
program on your PC (the **poller**) sees it and hands it to a **Claude Code "manager" session**
running in your project; that session's reply goes into a **second** list, which the phone reads
back to you. It is **not** a live phone call — messages arrive turn-by-turn, a little delayed, and
each is timestamped. That is normal and expected.

```
  PHONE (Claude app)                              PC
  ┌───────────────────────┐        ┌──────────────────────────────────────┐
  │  "To Project One"     │ ─────▶ │  poller  ─▶  to-manager.md  ─▶ manager │
  │   (you send here)     │        │                                session │
  │                       │        │                                  │     │
  │  "From Project One"   │ ◀───── │  to-phone.md  ◀──────────────────┘     │
  │   (replies appear)    │        │   (poller drains this back to the list)│
  └───────────────────────┘        └──────────────────────────────────────┘
        one such PAIR of lists per project · one poller per project
```

**Each project gets its own pair of lists and its own poller.** The phone assistant holds a small
**routing table** that maps a project to its pair of lists, so it knows which list to drop your
request into and which lists to read replies from.

---

## 2. The pieces — what files and lists must exist

For **each** project you want to control by voice, three things must line up:

1. **A project folder on your PC** containing `.claude/voice-bridge.json` — the config. It names:
   - `inbox_list` — the Reminders list the **phone sends to** (e.g. `To Project One`)
   - `output_list` — the Reminders list **replies appear in** (e.g. `From Project One`)
   - `mailbox_dir` — a folder on the PC where this project's mailbox files live
   - `name` / `from_name` — labels that keep this project's bookkeeping separate from others
   - locations (never the secrets themselves) of your Apple creds under `~/.auth`
2. **Two Reminders lists on the phone**, named **exactly** as `inbox_list` and `output_list`.
   (The Claude app / poller can put items *into* lists but **cannot create lists** — you make them
   by hand in the Reminders app, once.)
3. **A running poller and manager session** for that project on the PC.

The two example configs already exist:

- `examples/project-one/.claude/voice-bridge.json` → lists `To Project One` / `From Project One`
- `examples/project-two/.claude/voice-bridge.json` → lists `To Project Two` / `From Project Two`

### Why each project's names must be DISTINCT

The poller writes two mailbox files inside `mailbox_dir`: `to-manager.md` (things coming IN from
the phone) and `to-phone.md` (replies going OUT). These mailbox files are **not** auto-separated by
project. So if two projects shared one `mailbox_dir`, their two managers would read the **same**
`to-manager.md` and get each other's messages. That is why each example project has:

- **distinct list names** (`To Project One` vs `To Project Two`) — so each poller reads only its own
  inbox, and the phone can tell which project a reply is from; and
- a **distinct `mailbox_dir`** (`~/.claude/message-protocol/project-one` vs `.../project-two`) — so
  the two managers never see each other's mail.

One thing is safely **shared**: your Apple login. All projects point `creds_env` / `cookie_dir` at
the same `~/.auth` files, so you log in once and every project's poller uses that one session.

---

## 3. One-time setup (do this once, ever)

> **Using the recommended Radicale path (section 4)?** Skip this Apple-specific setup — your one-time
> setup is instead: run the Radicale server, seed `~/.auth/radicale.env`, and add the one CalDAV
> account to the phone, all in [`../radicale/OWNER-SETUP.md`](../radicale/OWNER-SETUP.md). The rest
> of this section is for the **iCloud** transport only.

You need the Apple credentials in place and a trusted session seeded. This is the same one-time
setup as the single-project version — see [`OWNER-SETUP.md`](OWNER-SETUP.md) for the full detail.
In short:

1. Create `~/.auth/icloud.env` with your **main** Apple ID and password:
   ```
   ICLOUD_APPLE_ID=you@icloud.com
   ICLOUD_PASSWORD=your-main-apple-id-password
   ```
2. Create `~/.auth/ntfy-topic.txt` with one private topic string, and subscribe the phone's
   **ntfy** app to that same topic (this is how long content pushes a tappable banner).
3. Seed the trusted session (needs a one-time 6-digit 2FA code):
   ```
   uv run /path/to/voice-bridge/pyicloud_login.py
   ```
   Good for ~60 days. Re-run only when the bridge later says "session needs 2FA".

`uv run` reads each script's inline dependencies and installs them into a throwaway environment —
there is no virtualenv for you to manage. (Install `uv` first if you don't have it.)

---

## 4. RECOMMENDED — Radicale, fully self-provisioning: `claude @SETUP-RADICALE.md`

There are two "one file, one command" templates. **The recommended one is the Radicale template**,
because it removes the last manual step entirely.

**Why Radicale is the recommended multi-project path.** The catch with iCloud is that the Claude
app / poller can put items *into* a Reminders list but **cannot create the list** — so every new
iCloud project still needs you to open the phone and make two lists by hand. A self-hosted
**Radicale** CalDAV server has no such restriction: a CalDAV client is allowed to **create
collections**. So on Radicale, onboarding a project is genuinely **zero manual server/phone steps** —
the setup creates the two lists *for you*, on the server, and they sync to the phone automatically.

|                              | **Radicale (recommended)**          | iCloud (`@SETUP.md`)                |
|------------------------------|-------------------------------------|-------------------------------------|
| Create the two lists         | **automatic** (server-side)         | **manual** — you make them on the phone |
| Per-project phone action     | **none**                            | create 2 Reminders lists by hand    |
| One-time account setup       | one Radicale CalDAV account on the phone | Apple creds + 2FA (section 3)  |

**One account, many projects — only the list names + `mailbox_dir` differ.** All Radicale projects
share the **one** Radicale CalDAV account already added to the phone and the **one** `~/.auth`
(`~/.auth/radicale.env` carries the server URL + user/pass; `~/.auth/ntfy-topic.txt` the push topic).
A new project changes only its **list names** (`To <Title>` / `From <Title>`) and its
**`mailbox_dir`** — nothing account-level is re-done.

The flow:

1. Copy **`SETUP-RADICALE.md`** (repo root) into your project folder.
2. In that folder, run **`claude @SETUP-RADICALE.md`**.

**Same file, same command for the FIRST launch and EVERY relaunch:**

- **First launch:** the template finds the voice-bridge repo once and runs `radicale_bootstrap.py`,
  which in ONE step derives this project's **distinct** list names from the folder name, writes
  `.claude/voice-bridge.json` (with `creds_env` → `~/.auth/radicale.env`, and `vb_path` recorded so
  nothing ever searches again), **and connects to Radicale to create the two VTODO lists**. No phone
  step.
- **Every relaunch:** the config already exists, so it reads `vb_path` and goes straight to starting
  the poller and listening.

You can preview / run the self-provision directly (safe to re-run — idempotent):

```
uv run /path/to/voice-bridge/radicale_bootstrap.py     # from inside your project folder
```

It prints `PROVISIONED: <slug>` (first run) or `ALREADY PROVISIONED: <name>` (when the config and
both lists already exist), plus the resolved fields and the start command. The one-time **server**
setup (run Radicale, seed `~/.auth/radicale.env`, add the phone's CalDAV account) is in
[`../radicale/OWNER-SETUP.md`](../radicale/OWNER-SETUP.md) — done once, for all Radicale projects.

### 4-icloud. Alternative — iCloud, one command with a manual list step: `claude @SETUP.md`

If you're on the iCloud transport instead, the repo ships **`SETUP.md`** (repo root) — the same
"one file, one command" shape, with the one unavoidable difference that **you must create the two
Reminders lists on the phone by hand** (the poller cannot create iCloud lists):

1. Copy `SETUP.md` into your project folder. 2. Run **`claude @SETUP.md`**.

- **First launch:** `SETUP.md` runs `bootstrap.py`, which derives distinct list names and writes
  `.claude/voice-bridge.json` (recording `vb_path`), then tells you the **one manual step** — create
  the two Reminders lists it names — and waits for you to confirm.
- **Every relaunch:** it reads `vb_path` from the config and goes straight to starting the poller.

Preview it with `uv run /path/to/voice-bridge/bootstrap.py` (prints `CONFIGURED` / `ALREADY
CONFIGURED` + the two list names to create). The one-time Apple-account setup (section 3) is still
required once, for all iCloud projects.

The manual per-project flow in sections **4-old through 7** below remains the explicit **fallback**
if you'd rather drive it by hand (or need to understand exactly what the templates automate).

---

## 4-old. Start ONE project by hand (project-one) — manual fallback

> **This section and sections 5-7 are the MANUAL fallback.** The recommended path is section 4
> (`claude @SETUP.md`) above, which automates exactly what these sections do by hand.

Starting a project has two clearly separated phases: a **ONE-TIME SETUP** you do once per project,
and an **EVERY LAUNCH** routine you repeat each session. (Section 3 above was the *account-level*
one-time setup — Apple creds — done once for *all* projects; this one is done once *per project*.)

### 4a. ONE-TIME SETUP (per project, do once)

1. **Create the project folder + its config.** project-one already ships
   `examples/project-one/.claude/voice-bridge.json`. Its `name`, `inbox_list`, `output_list`,
   `from_name`, and (crucially) `mailbox_dir` are all **distinct from every other project** so the
   pollers never cross wires (section 2). For a real project you copy an example config and change
   those fields — see section 7.
2. **Make its two lists on the phone.** In the Reminders app, create two lists named **exactly**
   `To Project One` and `From Project One`. (The app/poller can add items but **cannot create the
   list** — you make these two by hand, once. Use the equivalent Radicale lists for the CalDAV alt
   transport.)
3. **Save your own copy of the manager prompt with `$VB` already replaced.** Open
   `examples/project-one/MANAGER-PROMPT.md`, copy the fenced block, replace **every** `$VB` in it
   with your real voice-bridge path (e.g. `C:/Users/you/source/voice-bridge`), and save that
   already-substituted block where you can paste it each launch. You do the `$VB` edit **once, here**.
4. **First-run cost (informational).** The **first** poller run has `uv` install the script's inline
   dependencies into a cached environment — a one-time download. Every later run reuses the cache.

### 4b. EVERY LAUNCH (the lean routine)

1. **Open a fresh Claude Code session in the project-one folder** — the one that contains
   `.claude/voice-bridge.json`.
2. **Paste your saved (already-`$VB`-substituted) block.** It just starts the poller in the
   background and listens — no re-substituting, no verification, no reinstall. The exact command it
   runs (pinned to this project's config) is:
   ```
   uv run /path/to/voice-bridge/pyicloud_bridge.py --config ./.claude/voice-bridge.json
   ```
   Leave that poller running for the whole session.

At this point, anything you add to the `To Project One` list on the phone shows up (within the
interval) in `~/.claude/message-protocol/project-one/to-manager.md`, and the manager acts on it.

### 4c. COPY-PASTE WALKTHROUGH (beginner, step by step)

Exactly what to copy and paste the very first time:

1. **Copy the FENCED BLOCK** — the text **between the triple backticks** inside
   `examples/project-one/MANAGER-PROMPT.md` (it starts `You are the project-one MANAGER...`). Copy
   **only that block, NOT the whole file.**
2. **Replace every `$VB`** in the copied text with your real voice-bridge path
   (e.g. `C:/Users/you/source/voice-bridge`). This is the **one manual edit**. (Save this substituted
   copy — on later launches you paste it and skip this step.)
3. **Open a fresh Claude Code session inside the project-one folder** (the one with
   `.claude/voice-bridge.json`).
4. **Paste** the now `$VB`-free block. **Nothing needs editing after pasting** — it starts the poller
   and begins listening on its own.

> Optional sanity-check, any time you doubt which project a session binds to (not part of a normal
> launch): `uv run /path/to/voice-bridge/pyicloud_bridge.py --config ./.claude/voice-bridge.json --show-config`
> and confirm `name = project-one`, `inbox_list = To Project One`, `output_list = From Project One`,
> `mailbox_dir = ...\message-protocol\project-one`.

---

## 5. Add the SECOND project (project-two) — running side by side

Repeat section 4 (its ONE-TIME SETUP then EVERY LAUNCH) for project-two, in a **separate** Claude
Code session:

1. **One-time:** create `To Project Two` and `From Project Two` on the phone, and save your
   `$VB`-substituted copy of the fenced block from `examples/project-two/MANAGER-PROMPT.md`.
2. **Each launch:** open a Claude Code session **in the project-two folder** and paste that saved
   block. Its poller command uses project-two's config, so it watches only `To Project Two` and
   writes only into `~/.claude/message-protocol/project-two/`.

Because the two projects have distinct list names AND distinct mailbox dirs (section 2), the two
pollers and two managers run at the same time without ever touching each other's messages.

---

## 6. Set up the phone to route to BOTH projects

On the phone, save `examples/PHONE-ASSISTANT-PROMPT.md` (as a Claude **Project** custom
instruction, or paste it at the start of a voice chat). It contains a **ROUTING TABLE** that maps
each project to its list pair, plus a DEFAULT project for when you don't name one.

Now, when you speak:
- *"On project two, run the tests"* → the assistant drops it in `To Project Two`; project-two's
  poller picks it up; that manager runs the tests.
- Every turn, the assistant silently reads **both** `From Project One` and `From Project Two`, and
  reads any reply back to you **saying which project it's from** ("project two says: tests pass").
- If it can't tell which project you meant, it **asks** rather than guessing.

To roll back to a single project, delete every block in the routing table except the DEFAULT one.

---

## 7. ONBOARD A NEW PROJECT — the recipe

Say you have a real project, `my-app`, at `C:/Users/you/source/my-app`. To bring it under the same
phone assistant:

1. **Create the config.** Make `C:/Users/you/source/my-app/.claude/voice-bridge.json`. Copy an
   example config and change the four project-specific fields to be **distinct from every other
   project**:
   ```json
   {
     "name": "my-app",
     "inbox_list": "To My App",
     "output_list": "From My App",
     "from_name": "my-app-phone",
     "mailbox_dir": "~/.claude/message-protocol/my-app",
     "ntfy_topic_file": "~/.auth/ntfy-topic.txt",
     "creds_env": "~/.auth/icloud.env",
     "cookie_dir": "~/.auth/pyicloud-cookies",
     "poll_interval": 10
   }
   ```
   (Leave `creds_env` / `cookie_dir` / `ntfy_topic_file` pointing at the shared `~/.auth` files.)
2. **Create the two Reminders lists** on the phone, named **exactly** `To My App` and `From My App`
   (or set up the equivalent lists in Radicale if you use the CalDAV alt transport). The names must
   match the config character-for-character.
3. **Start the poller** from the project's folder, pinned to its config:
   ```
   uv run /path/to/voice-bridge/pyicloud_bridge.py --config ./.claude/voice-bridge.json
   ```
   (First run `--show-config` to confirm it resolved to the `my-app` values.) Give that Claude Code
   session a manager prompt — copy `examples/project-one/MANAGER-PROMPT.md` and swap the project
   name, the two list names, and the mailbox path to `my-app`'s.
4. **Add it to the phone routing table.** In `PHONE-ASSISTANT-PROMPT.md`, copy a `PROJECT:` block
   and fill in:
   ```
   PROJECT: my-app
     TO   list (I send here):     "To My App"
     FROM list (you read here):   "From My App"
   ```
   Save the updated prompt in the phone app. Done — now "on my app, ..." routes to your real project.

That's the whole loop: **folder + config (distinct names) → two lists → poller with `--config` →
one line in the routing table.** Each new project is that same four-step block.

---

## 8. When things look wrong (quick checks)

- **A request never arrives.** Confirm the list name on the phone matches `inbox_list` exactly
  (`--show-config`), and that the poller for that project is actually running.
- **The wrong session answers.** Two projects are sharing a `mailbox_dir` or a list name — make them
  distinct (section 2). Re-run `--show-config` on each.
- **Replies don't come back.** Confirm the manager is appending to *its* `to-phone.md` (the path the
  MANAGER-PROMPT shows) and that the poller is still up. Replies are timestamped and can lag a turn.
- **"session needs 2FA".** Re-run `pyicloud_login.py` once (section 3).
- **The private Apple API stops working.** Switch to the CalDAV/Radicale alt transport — same
  mailbox contract, one env var swap. See `../radicale/OWNER-SETUP.md`.
