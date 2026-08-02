# Notifications — make your phone buzz

Optional, and worth the two minutes. Without it replies still arrive in your Reminders list;
you just have to remember to look.

**Do this while `vb setup` is asking you about it** — the guides send you here at that exact
moment, because the test message right afterwards fires a banner, and you want to be
subscribed in time to see it.

- [1. Install the ntfy app](#1-install-the-ntfy-app)
- [2. Answer setup's question](#2-answer-setups-question)
- [3. Subscribe on the phone](#3-subscribe-on-the-phone)
- [Sending one by hand](#sending-one-by-hand)

---

## 1. Install the ntfy app

Banners are delivered by [ntfy](https://ntfy.sh) — install the app on your phone from the
App Store before you go further.

## 2. Answer setup's question

Setup offers a menu:

- a **private topic it generates for you** — nothing to type, and the right answer if you
  have no opinion;
- a topic **you already subscribe to**;
- **your own ntfy server**, and a topic on it;
- **skip**.

**It prints the generated topic on screen, just above the question** — a long `vox-…` string.
Copy it before answering.

> **A topic is a password, not a name.** Anyone who knows the string can read every reply you
> ever receive: there is no account, no login and no revocation. That is why the generated
> one is long and random, and why you should not shorten it to something memorable.

## 3. Subscribe on the phone

In the ntfy app, tap **+** to subscribe to a topic, paste the `vox-…` string exactly, and
confirm. Then go back to the terminal and answer setup's question.

**Proof:** the test message setup offers next fires a banner on your phone. If it does not,
you are either not subscribed to that exact string, or you chose *skip*.

## Sending one by hand

Any time, to check the path is alive:

```
vb notify "hello from the bridge"
```

Add a link that opens when the banner is tapped:

```
vb notify "build finished" --click https://example.com/run/42
```

Exit 2 means no topic is configured; exit 1 means one is configured and the send failed —
those used to look identical, so a network blip read as a missing setup.

**Changed your mind later?** Re-run `vb setup` — it offers the step again whenever no topic
is configured. The topic string itself is stored in `ntfy-topic.txt` in the state directory,
one line, so you can also read it back or replace it by editing that file; `vb config show`
prints its exact path. (`ntfy_topic_file` is a config field, but it names *where the file
lives* — it does not hold the topic.)

---

**Related:** [iCloud setup](setup-icloud.md) · [Radicale setup](setup-radicale.md) ·
[Using the bridge](using.md) · [README](../README.md)
