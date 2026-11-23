#!/usr/bin/env bash
# Reaction for the hello_opal intent: speaks a greeting through the opal-say
# ASGI mount (POST /say/api/v1/generate on this same listen service), played
# locally via the opal-say CLI. Falls back to eSpeak where the mount or CLI is
# unavailable. Match-result JSON arrives on stdin and is ignored; playback is
# spawned so the serial reaction queue is not blocked for its duration.
set -euo pipefail

TEXT="Hello! My name is Opal."
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${OPAL_LISTEN_API_URL:-http://127.0.0.1:8000}/say"

cat >/dev/null &   # drain the match-result JSON on stdin

# Prefer the opal-say CLI pointed at the mounted endpoint.
for cli in opal-say "$REPO/../opal-say/.venv/bin/opal-say" "$REPO/../opal-say/.venv/Scripts/opal-say.exe"; do
  if command -v "$cli" >/dev/null 2>&1 || [ -x "$cli" ]; then
    "$cli" --api-url "$BASE" generate "$TEXT" --play >/dev/null 2>&1 &
    exit 0
  fi
done

# Fall back to a local eSpeak binary.
for cmd in espeak espeak-ng espeech; do
  if command -v "$cmd" >/dev/null 2>&1; then
    "$cmd" "$TEXT" &
    exit 0
  fi
done

echo "hello_opal: no opal-say CLI or eSpeak binary found" >&2
exit 1
