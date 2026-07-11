# examples — two working projects behind one phone

This folder ships **two complete, runnable example projects** plus the single phone-side
prompt that manages both. Use them as the concrete reference when wiring up a real project.
Full beginner walkthrough: [`../docs/ONBOARDING.md`](../docs/ONBOARDING.md).

```
examples/
├── README.md                          ← you are here
├── PHONE-ASSISTANT-PROMPT.md          ← ONE phone prompt that routes to BOTH projects
├── project-one/
│   ├── .claude/voice-bridge.json      ← project-one config (distinct lists + mailbox)
│   └── MANAGER-PROMPT.md              ← paste into a Claude Code session in project-one/
└── project-two/
    ├── .claude/voice-bridge.json      ← project-two config (distinct lists + mailbox)
    └── MANAGER-PROMPT.md              ← paste into a Claude Code session in project-two/
```

## What is distinct per project — and WHY it must be

Each project runs its **own poller** against its **own** pair of Reminders lists. For two
pollers to run side by side on one PC **without crossing wires**, three things must be
distinct per project (the two example configs already are):

| field | project-one | project-two | why distinct |
|-------|-------------|-------------|--------------|
| `inbox_list` | `To Project One` | `To Project Two` | each poller reads ONLY its own inbox list, so a phone request lands with exactly one project |
| `output_list` | `From Project One` | `From Project Two` | replies from each project surface on their own list, so the phone can say which project answered |
| `mailbox_dir` | `~/.claude/message-protocol/project-one` | `~/.claude/message-protocol/project-two` | **the file mailbox is NOT namespaced** (see below) — distinct dirs keep the two managers from reading each other's `to-manager.md` |

**The mailbox-collision trap (important).** Inside a config's `mailbox_dir` the poller writes
`to-manager.md` (inbound) and `to-phone.md` (outbound). Only the tiny dedupe *seen-files* are
auto-namespaced by project name (`state/<name>-seen.txt`); the **mailbox `.md` files are not**.
So if both projects used the default `mailbox_dir`, project-one's manager and project-two's
manager would both read the **same** `to-manager.md` and see each other's messages. Giving each
project its own `mailbox_dir` (as these examples do) is what keeps them cleanly separated.

`name`/`from_name` are also distinct (`project-one` / `project-one-phone`, etc.) so the seen-files
never collide and each mailbox line is tagged with which project's phone it came from.

## What is SHARED across projects (and safely so)

`creds_env`, `cookie_dir`, and `ntfy_topic_file` all point at the same `~/.auth` files. One Apple
login (one trusted session) serves every project's poller — you seed 2FA once, not per project.

## RECOMMENDED — Radicale, which provisions each project with ZERO manual steps

The two example configs above use **iCloud**, where the poller can add items to a list but
**cannot create the list** — so each new iCloud project still needs you to make its two Reminders
lists on the phone by hand. The **recommended** transport for a multi-project setup is a self-hosted
**Radicale** CalDAV server, because a CalDAV client *is* allowed to create collections. That makes
onboarding a project fully self-provisioning:

```
uv run /path/to/voice-bridge/radicale_bootstrap.py     # run from inside the project folder
```

In one idempotent step it derives the project's distinct list names from the folder name, writes
`.claude/voice-bridge.json` (with `creds_env` → `~/.auth/radicale.env`), **and connects to Radicale
to create the two lists** `To <Title>` / `From <Title>`. Re-running prints `ALREADY PROVISIONED`.
There is **no phone step** — because every Radicale project shares the ONE Radicale CalDAV account
already added to the phone, the new lists sync to Reminders automatically.

**One account + one `~/.auth`, many projects — only the list names and `mailbox_dir` differ per
project** (exactly the distinct-fields table above; the shared account is the Radicale one, not an
Apple one). Drive it hands-off with the `claude @SETUP-RADICALE.md` template (repo root), the
Radicale sibling of `SETUP.md`. Server-side one-time setup: [`../radicale/OWNER-SETUP.md`](../radicale/OWNER-SETUP.md).

Contrast: iCloud (`claude @SETUP.md`) is identical **except** it stops to ask you to create the two
Reminders lists on the phone by hand; Radicale creates them for you.

## Run it

1. Create the four Reminders lists on the phone (exact names above): `To Project One`,
   `From Project One`, `To Project Two`, `From Project Two`.
2. In a Claude Code session opened **in `project-one/`**, paste `project-one/MANAGER-PROMPT.md`.
3. In a **separate** Claude Code session opened **in `project-two/`**, paste
   `project-two/MANAGER-PROMPT.md`.
4. On the phone, save `PHONE-ASSISTANT-PROMPT.md`. Now say "on project one, ..." or
   "tell project two to ..." and it routes to the right session.

Step-by-step from zero (including `~/.auth` + login): [`../docs/ONBOARDING.md`](../docs/ONBOARDING.md).
