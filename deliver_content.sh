#!/usr/bin/env bash
# Deliver READABLE content to the owner's phone/computer, alongside voice + ntfy.
#
# Publishes a Markdown file as a GitHub gist (renders on phone + desktop), pushes a
# tappable ntfy notification with the link, and drops a one-line summary + link into
# the output ("From Claude") voice channel (so the phone assistant can read a short
# walkthrough instead of the owner reading everything). Both modes, every time.
#
# Config-driven: the ntfy topic file and the reply path come from the resolved
# voice-bridge config (config.py). Override the config with VOICE_BRIDGE_CONFIG or
# --config PATH (passed straight through to the python helper).
#
# Usage: deliver_content.sh [--config PATH] <markdown-file> "<one-line summary>"
#
# Reliability: gist + ntfy are both plain HTTPS; the reply drop uses the same
# pyicloud path as replies. gh must be authed; ntfy topic file per the config.
set -euo pipefail

CONFIG_ARG=()
if [ "${1:-}" = "--config" ]; then
  CONFIG_ARG=(--config "$2"); shift 2
fi

MD="${1:?usage: deliver_content.sh [--config PATH] <markdown-file> \"<summary>\"}"
SUMMARY="${2:?usage: deliver_content.sh [--config PATH] <markdown-file> \"<summary>\"}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Resolve the ntfy topic file from the config (python prints the absolute path).
TOPIC_FILE="$(cd "$HERE" && python - "${CONFIG_ARG[@]}" <<'PY'
import sys
from config import load_config
argv = sys.argv[1:]
path = argv[1] if len(argv) >= 2 and argv[0] == "--config" else None
print(load_config(path).ntfy_topic_file)
PY
)"
TOPIC="$(cat "$TOPIC_FILE")"

# 1) publish as a secret (unlisted) gist -> readable link
URL="$(gh gist create "$MD" --desc 'Claude Code: content to review' 2>&1 \
        | grep -o 'https://gist.github.com/[^ ]*' | head -1)"
[ -n "$URL" ] || { echo "ERROR: gist creation failed" >&2; exit 1; }

# 2) ntfy: tappable notification (sound + banner) that opens the content.
#    Keep the BODY short + complete (iOS banners clip ~150 chars mid-word).
BODY="$SUMMARY"
if [ "${#BODY}" -gt 130 ]; then BODY="$(printf '%s' "$BODY" | cut -c1-129 | sed 's/ [^ ]*$//')…"; fi
curl -s -o /dev/null -w 'ntfy: HTTP %{http_code}\n' \
  -H "Title: Claude Code — content to review" \
  -H "Priority: high" -H "Tags: page_facing_up" \
  -H "Click: $URL" \
  -d "$BODY  (tap to open)" \
  "https://ntfy.sh/$TOPIC"

# 3) voice channel: FULL TEXT + link into the output list (notify=False -> no dup beep).
#    The full text is inline because the voice assistant has NO web access to a private
#    gist — it can only read aloud what is in the reminder. The link is for the owner.
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run --with pyicloud python - "$HERE" "$URL" "$SUMMARY" "$MD" "${CONFIG_ARG[@]}" <<'PY'
import sys
here, url, summary, md = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
config_path = sys.argv[6] if len(sys.argv) >= 7 and sys.argv[5] == "--config" else None
sys.path.insert(0, here)
import pyicloud_bridge as B
from config import load_config
cfg = load_config(config_path)
content = open(md, encoding="utf-8").read()
B.send_reply(
    cfg,
    f"{summary}\n\nFULL CONTENT (ask me to read this aloud):\n{content}\n\n(Open it yourself: {url})",
    notify=False,
)
PY

echo "DELIVERED: $URL"
