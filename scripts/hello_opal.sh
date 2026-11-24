#!/usr/bin/env bash
# Reaction for the hello_opal intent: speak a greeting via OPAL Say.
# Tries the standalone opal-say service first, then the ASGI mount under the
# listen service; falls back to a local eSpeak binary. The match-result JSON
# arrives on stdin and is drained/ignored. Runs in the foreground so the
# reaction journal records the real exit code and output.
set -euo pipefail

TEXT="Hello! My name is Opal."
REPO="$(cd "$(dirname "$0")/.." && pwd)"
URLS=(
  "${OPAL_SAY_API_URL:-http://127.0.0.1:8001}"
  "${OPAL_LISTEN_API_URL:-http://127.0.0.1:8000}/say"
)

cat >/dev/null &   # drain the match-result JSON on stdin (unused)

for cli in "${OPAL_SAY_CLI:-}" opal-say \
           "$REPO/../opal-say/.venv/Scripts/opal-say.exe" \
           "$REPO/../opal-say/.venv/bin/opal-say"; do
  [ -n "$cli" ] || continue
  command -v "$cli" >/dev/null 2>&1 || [ -x "$cli" ] || continue
  for url in "${URLS[@]}"; do
    "$cli" --api-url "$url" generate "$TEXT" --play && exit 0
  done
done

for cmd in espeak espeak-ng espeech; do
  if command -v "$cmd" >/dev/null 2>&1; then
    "$cmd" "$TEXT"
    exit 0
  fi
done

echo "hello_opal: no working speech path (opal-say CLI and eSpeak both failed)" >&2
exit 1
