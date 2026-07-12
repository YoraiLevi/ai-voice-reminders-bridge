# Message-delivery reliability — the "silently completed" vulnerability + proposed design

**Status:** PROPOSAL (owner to choose a direction — nothing built yet).
**Trigger:** owner, 2026-07-12 08:57 — "the script exhausted them as completed while your context was
full, making them not read to you; we will need a robust architecture and instructions to handle issues."

## Recovery finding first (what's actually lost right now)
Checked all three lists titled "To Claude" (there are three — a ghost problem in itself):

| list id (short) | count | incomplete (unread) | meaning |
|---|---|---|---|
| `A57F0F43` | 62 | **0** | old ghost list — all completed (pre-migration consumed) |
| `F1F63066` | 1 | **0** | previously-pinned list — 1 completed |
| `1A971861` (live, pinned) | 15 | 3 | the 3 are 08:31/08:33/08:38, already answered |

**No unread (incomplete) message is stranded on any list.** Everything sent since 08:00 is in the
manager's mailbox file and has been answered. The "mistakenly completed" items are the pre-migration
ones on the two old lists; their *text* isn't in the current iCloud mailbox file (which starts 08:00) —
recovering it would need the phone or the older Radicale/transcript records.

## The real vulnerability (why the owner is right to want a redesign)
The poller marks an inbox item **completed the moment it copies the text into the manager's mailbox
file** — *before* the manager has actually read or acted on it. "Completed" is being used as a
read-receipt, but it means "the poller saw it," not "the manager handled it."

So if the manager session is **dead, compacting, or context-full** in that window, the message is
consumed on the phone (marked done) yet never acted on. Silent loss — and it looks identical to
"handled."

**Concrete evidence it's already fragile:** three items I *have* answered (08:31/33/38) are still sitting
**incomplete** on the live list, while later ones (08:55/57) are completed. The append-to-file step and
the mark-complete step are **not atomic**, so a crash between them leaves the phone and the file
disagreeing in *either* direction.

Two distinct defects:
1. **Consume-decoupled-from-process** — mark-complete happens at poller-read, not manager-done.
2. **Duplicate "To Claude" lists** (3 of them) — ghosts from delete+recreate; auto-heal picks by
   unread-count, which is a fragile signal if two ever have unread items at once.

## Proposed design (options — owner picks)

### Option A — ACK-on-process (recommended core)
The poller **stops marking items complete on read.** It appends to the mailbox file and leaves the
reminder **incomplete**, tracking it in a pending set by reminder id. The **manager** marks the item
complete **only after it has processed and replied.**
- **Win:** an unprocessed message stays visibly *pending on the phone* — nothing is silently consumed.
  After any manager death, the still-incomplete items ARE the backlog to work.
- **Cost:** manager needs a per-message "done" signal + an id↔message map; a mis-behaving manager could
  leave items incomplete forever (mitigated by B's watchdog).

### Option B — Durable replay cursor + watchdog (recommended companion)
Keep the mailbox file as the append-only log (it already is). The manager persists a **last-processed
cursor**; on **every wake/resume** it replays any mailbox lines after the cursor — so a session that
died mid-window catches up automatically. A **watchdog** re-sends an ntfy if any line stays unacked
past N minutes ("3 unprocessed messages waiting").
- **Win:** guarantees the manager eventually processes everything, even across compaction/death.
- **Cost:** cursor bookkeeping; doesn't make the *phone* show pending (the item may already be
  completed) — that's what A adds.

### Recommendation: **A + B together.**
A gives phone-visible pending state (the human sees unhandled messages); B guarantees the manager
catches up after any death. Neither alone is sufficient: A without B can strand items if the manager
never wakes; B without A leaves the phone showing "done" for things not yet handled.

### Orthogonal but required: de-duplicate the lists
There must be exactly **one** "To Claude" (and one "From Claude"). pyicloud can't delete lists, so the
**owner deletes the two old ones on the phone** (`A57F0F43`, `F1F63066`), leaving `1A971861`; then we
re-pin its id and the auto-heal ambiguity disappears.

## Instructions / runbook ("handle issues")
- **One list per channel name.** If you recreate a channel list, tell the manager to re-pin its id and
  delete the old one — don't leave duplicates.
- **On any "did you get my message?" doubt:** the manager runs the recovery scan (list every same-titled
  list + incomplete counts; read the mailbox file tail) before answering — never infer from memory.
- **Until Option A ships:** completion is NOT proof of processing. Treat a completed item as "seen by
  the poller," and confirm the manager actually replied.
- **Manager survival:** the manager must re-arm its inbox Monitor + heartbeat on every resume, and on
  resume replay the mailbox from its cursor (Option B) before starting new work.
