#!/usr/bin/env bash
# opal-listen CLI smoke test — exercises the command surface end to end.
# Prereq: the service is running (`opal-listen-svc`, default http://127.0.0.1:8000).
# Override the target with OPAL_LISTEN_API_URL if needed.
set -euo pipefail
cd "$(dirname "$0")/.."

# Activate the virtualenv (Linux path first, Windows Git Bash path second)
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate

# --- Health and identity ---
opal listen system ping
opal listen system info
opal listen system doctor

# --- Catalog reads ---
opal listen wakewords list
opal listen intents list
opal listen slots list
opal listen profiles list
opal listen settings list

# --- A wakeword write round-trip (ETag-backed PATCH + DELETE) ---
# Multi-word wakewords must be quoted — names are normalized to spoken form
opal listen wakewords add "smoke test word" --threshold 0.7
opal listen wakewords remove "smoke test word"

# --- Recognition over text (no microphone needed) ---
opal listen test text "what time is it" --no-save
opal listen test text "turn on the kitchen light" --no-save

# --- Recognition logs written by the calls above and earlier traffic ---
opal listen logs count
opal listen logs list --limit 3

# --- Reactions: file-managed commands fired on intent match ---

# Show which files this CLI manages
opal listen reactions path

# Empty to start
opal listen reactions list

# Arm a reaction: when light_turn_on matches confidently with room=kitchen,
# run `python -m json.tool`, which echoes the match JSON it gets on stdin
opal listen reactions add kitchen_lights \
    --cond "intent_name eq light_turn_on" \
    --cond "slot_values.room_name eq kitchen" \
    --cond "intent_confidence gte 0.8" \
    --run "python -m json.tool" \
    --timeout 15

opal listen reactions list
opal listen reactions get kitchen_lights

# Dry-run against a literal payload — shows ✓/✗ per reaction with the failing condition
opal listen reactions test --payload '{"intent_name": "light_turn_on", "slot_values": {"room_name": "kitchen"}, "intent_confidence": 0.9}'
opal listen reactions test --payload '{"intent_name": "light_turn_on", "slot_values": {"room_name": "den"}}'

# Dry-run with a REAL utterance through the live pipeline (needs the service)
opal listen reactions test --text "turn on the kitchen light"

# Actually run the matching command (stdin JSON -> python -m json.tool echoes it).
# Note: the payload must satisfy every condition — a missing field (e.g. no
# intent_confidence here) makes the reaction NOT match and nothing executes.
opal listen reactions test --payload '{"intent_name": "light_turn_on", "slot_values": {"room_name": "kitchen"}, "intent_confidence": 0.9}' --execute

# Update: rename + retime; toggle off and on
opal listen reactions update kitchen_lights --name lights_kitchen --timeout 30
opal listen reactions disable lights_kitchen
opal listen reactions enable lights_kitchen

# Turn firing on for the running service (a mic match would now run the command),
# confirm, then switch it back off
opal listen settings set reactions_enabled true
opal listen settings get reactions_enabled
opal listen settings set reactions_enabled false

# Disarm the reaction so the smoke run leaves no state behind
opal listen reactions remove lights_kitchen

# Execution journal (may not exist yet — only live-mic matches write it)
opal listen reactions log --limit 5 || true

# --- Config bundle validation (the six YAML files; reactions.yaml is intentionally outside it) ---
opal listen config validate

# --- The live command tree ---
opal listen tree

echo "smoke test complete"
