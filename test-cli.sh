#!/usr/bin/env bash
# test-cli.sh - call every opal_listener REST operation through the `opal listen` CLI.
#
# Prints one line per test (PASS / FAIL / SKIP), a summary, and a coverage check that compares the
# operations tested here with the server's /openapi.json. Exits 1 if any test failed.
#
# Usage:
#   ./test-cli.sh                  start an isolated server (temporary database) and test it
#   ./test-cli.sh http://pi:8000   test a running server; its state is backed up and restored at the end
#
# Environment:
#   OPAL_CLI                       CLI command (default: python3 -m opal_listener.cli from this checkout)
#   OPAL_LISTENER_VOSK_MODEL_PATH  Vosk model used by the isolated server (the audio test skips without it)
#   VERBOSE=1                      print the output of every command
#
# Each test is: t "<description>" "<METHOD /path>" "<text expected in output>" <cli args...>
#   tf   same, but the command must FAIL (validation errors)
#   RAW=1 t ...     run without --json;   RE=1 t ...     expected text is an extended regex
#   ANY_EXIT=1 t ...  accept a non-zero exit code (only the expected text is checked)

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export COLUMNS=200 NO_COLOR=1
read -r -a OPAL <<<"${OPAL_CLI:-python3 -m opal_listener.cli}"
TMP="$(mktemp -d /tmp/test-cli.XXXXXX)"
WAV="$REPO/opal_listener/tests/audio/fixtures/golden_kitchen_light_on.wav"
TAG="$(tr -dc a-z </dev/urandom | head -c 4)"
WW="hey smoke $TAG"                 # names are unique per run so a shared server is not disturbed
SLOT="smoke_${TAG}_color"
INTENT="smoke_${TAG}_paint"
PROFILE="smoke_${TAG}_profile"
PASSED=0 FAILED=0 SKIPPED=0 N=0
FAILED_TESTS=() TESTED=()
SERVER_PID=""
OUT=""

cleanup() {
    [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null && wait "$SERVER_PID" 2>/dev/null
    rm -rf "$TMP"
}
trap cleanup EXIT

# --- helpers ------------------------------------------------------------------------------------

# run the CLI; output goes to $OUT (stdout+stderr, newlines and repeated spaces collapsed)
cli() {
    local args=(listen --api-url "$API")
    [[ "${RAW:-0}" == 1 ]] || args+=(--json)
    OUT="$("${OPAL[@]}" "${args[@]}" "$@" 2>&1)"
    local code=$?
    OUT="$(printf '%s' "$OUT" | sed 's/│/ /g' | tr '\n' ' ' | tr -s ' ')"
    [[ "${VERBOSE:-0}" == 1 ]] && printf '      $ opal %s\n      %s\n' "${args[*]} $*" "$OUT"
    return $code
}

contains() {
    if [[ "${RE:-0}" == 1 ]]; then grep -qE -- "$1" <<<"$OUT"; else grep -qF -- "$1" <<<"$OUT"; fi
}

report() { # report STATUS DESCRIPTION [DETAIL]
    N=$((N + 1))
    case "$1" in
        PASS) PASSED=$((PASSED + 1)); printf '\033[32mPASS\033[0m %02d %s\n' "$N" "$2" ;;
        SKIP) SKIPPED=$((SKIPPED + 1)); printf '\033[33mSKIP\033[0m %02d %s (%s)\n' "$N" "$2" "$3" ;;
        FAIL) FAILED=$((FAILED + 1)); FAILED_TESTS+=("$N $2")
              printf '\033[31mFAIL\033[0m %02d %s\n     %s\n' "$N" "$2" "${3:0:400}" ;;
    esac
}

t() { # test that must succeed
    local desc="$1" op="$2" expect="$3"; shift 3
    TESTED+=("$op")
    cli "$@"; local code=$?
    if [[ $code -ne 0 && "${ANY_EXIT:-0}" != 1 ]]; then report FAIL "$desc [$op]" "exit $code: $OUT"
    elif ! contains "$expect"; then report FAIL "$desc [$op]" "expected '$expect' in: $OUT"
    else report PASS "$desc [$op]"; fi
}

tf() { # test that must be rejected
    local desc="$1" op="$2" expect="$3"; shift 3
    TESTED+=("$op")
    if cli "$@"; then report FAIL "$desc [$op]" "expected a non-zero exit code: $OUT"
    elif ! contains "$expect"; then report FAIL "$desc [$op]" "expected '$expect' in: $OUT"
    else report PASS "$desc [$op]"; fi
}

skip() { TESTED+=("$2"); report SKIP "$1 [$2]" "$3"; }

# evaluate a Python expression on the JSON in $OUT (d = parsed document), e.g. json_get 'd["id"]'
json_get() { python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print($1)" <<<"$OUT" 2>/dev/null; }

# --- server -------------------------------------------------------------------------------------

if [[ $# -ge 1 ]]; then
    API="${1%/}"
    echo "Testing existing server $API (state is restored from a backup at the end)"
else
    PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
    API="http://127.0.0.1:$PORT"
    (cd "$REPO" && OPAL_LISTENER_DATA_DIR="$TMP/data" OPAL_LISTENER_HOST=127.0.0.1 OPAL_LISTENER_PORT="$PORT" \
        exec python3 -m opal_listener >"$TMP/server.log" 2>&1) &
    SERVER_PID=$!
    echo "Starting isolated server $API (log: $TMP/server.log)"
    for _ in $(seq 120); do curl -fs "$API/healthz" >/dev/null && break; sleep 0.5; done
fi
curl -fs "$API/healthz" >/dev/null || { echo "server at $API is not reachable"; exit 1; }
echo "Run tag: $TAG"
echo

# --- system ---------------------------------------------------------------------------------------

# liveness probe
t "system ping" "GET /healthz" '"status": "ok"' system ping
# service name and version
t "system info" "GET /" '"name": "opal_listener"' system info
# startup health checks; exit code is 1 when a check reports an error, so only the report is checked
ANY_EXIT=1 t "system doctor" "GET /api/v1/health/" '"checks"' system doctor
MODEL_OK=0; grep -qF '"name": "vosk_model", "status": "ok"' <<<"$OUT" && MODEL_OK=1
# audio input devices visible to the server
t "system devices" "GET /api/v1/health/audio_devices" '' system devices

# --- backups (taken first so the run can be undone) --------------------------------------------------

# existing backups, remembered so only this run's backups are deleted at the end
t "config backups" "GET /api/v1/backup/list" '' config backups
BACKUPS_BEFORE="$(grep -oE 'opal_listener_[A-Za-z0-9_]+\.db' <<<"$OUT" | sort -u)"
# safety backup of the current state
t "config backup" "POST /api/v1/backup/create" '"filename"' config backup
BACKUP="$(json_get 'd["filename"]')"

# --- settings -------------------------------------------------------------------------------------

# all settings as key/value pairs
t "settings list" "GET /api/v1/settings/" '"cooldown_ms"' settings list
# one setting
t "settings get" "GET /api/v1/settings/{key}" '"key": "cooldown_ms"' settings get cooldown_ms
# change a setting
t "settings set" "PUT /api/v1/settings/{key}" '"value": "1234"' settings set cooldown_ms 1234
# invalid values are rejected
tf "settings set rejects bad value" "PUT /api/v1/settings/{key}" 'cooldown_ms' settings set cooldown_ms soon
# reset to the default
t "settings reset" "DELETE /api/v1/settings/{key}" '"status": "ok"' settings reset cooldown_ms

# --- wakewords ------------------------------------------------------------------------------------

# create a wakeword
t "wakewords add" "POST /api/v1/wakewords/" "\"name\": \"$WW\"" wakewords add "$WW" --threshold 0.75
# list wakewords
t "wakewords list" "GET /api/v1/wakewords/" "\"$WW\"" wakewords list
# get one wakeword (by name)
t "wakewords get" "GET /api/v1/wakewords/{wakeword_id}" '"confidence_threshold": 0.75' wakewords get "$WW"
# update threshold and disable it
t "wakewords update" "PATCH /api/v1/wakewords/{wakeword_id}" '"enabled": false' \
    wakewords update "$WW" --threshold 0.65 --disabled

# --- slots ----------------------------------------------------------------------------------------

# create a list slot
t "slots add" "POST /api/v1/slots/" "\"name\": \"$SLOT\"" slots add "$SLOT" --description "smoke colours"
# add values
t "slots add-value red" "POST /api/v1/slots/{slot_id}/values" '"value": "red"' slots add-value "$SLOT" red
t "slots add-value blue" "POST /api/v1/slots/{slot_id}/values" '"value": "blue"' slots add-value "$SLOT" blue
# add an alias that normalizes to a value
t "slots add-alias" "POST /api/v1/slots/{slot_id}/values/{value_id}/aliases" '"alias": "crimson"' \
    slots add-alias "$SLOT" red crimson
# list slots
t "slots list" "GET /api/v1/slots/" "\"$SLOT\"" slots list
# get one slot with values and aliases
t "slots get" "GET /api/v1/slots/{slot_id}" '"alias": "crimson"' slots get "$SLOT"
# list values of a slot
t "slots values" "GET /api/v1/slots/{slot_id}/values" '"value": "blue"' slots values "$SLOT"
# list aliases of a value
t "slots aliases" "GET /api/v1/slots/{slot_id}/values/{value_id}/aliases" '"alias": "crimson"' \
    slots aliases "$SLOT" red
# update the slot
t "slots update" "PATCH /api/v1/slots/{slot_id}" '"description": "updated"' slots update "$SLOT" --description updated

# --- intents --------------------------------------------------------------------------------------

# create an intent with a training phrase that uses the slot
t "intents add" "POST /api/v1/intents/" "\"name\": \"$INTENT\"" \
    intents add "$INTENT" --phrase "paint the wall {$SLOT}"
# list intents
t "intents list" "GET /api/v1/intents/" "\"$INTENT\"" intents list
# get one intent with phrases
t "intents get" "GET /api/v1/intents/{intent_id}" "paint the wall {$SLOT}" intents get "$INTENT"
# add a phrase
t "intents add-phrase" "POST /api/v1/intents/{intent_id}/phrases" "color it {$SLOT}" \
    intents add-phrase "$INTENT" "color it {$SLOT}"
# a phrase with an unknown slot is rejected before it is saved
tf "intents add-phrase rejects unknown slot" "POST /api/v1/intents/{intent_id}/phrases" 'unknown slot' \
    intents add-phrase "$INTENT" "paint {ghost_slot}"
# list phrases
t "intents phrases" "GET /api/v1/intents/{intent_id}/phrases" "color it {$SLOT}" intents phrases "$INTENT"
# update the intent
t "intents update" "PATCH /api/v1/intents/{intent_id}" '"confidence_threshold": 0.8' \
    intents update "$INTENT" --threshold 0.8
# recognize text: the alias 'crimson' becomes slot value 'red'
t "test text" "POST /api/v1/recognition/test_text" "\"$SLOT\": \"red\"" \
    test text "paint the wall crimson" --expect-intent "$INTENT"
# remove a phrase (by text)
t "intents remove-phrase" "DELETE /api/v1/intents/{intent_id}/phrases/{phrase_id}" '"status": "ok"' \
    intents remove-phrase "$INTENT" "color it {$SLOT}"

# --- profiles -------------------------------------------------------------------------------------

# list profiles and remember the active one
t "profiles list" "GET /api/v1/profiles/" '"is_active"' profiles list
ORIG_PROFILE="$(json_get 'next((p["name"] for p in d if p["is_active"]), "")')"
# create a profile limited to the new intent and slot
t "profiles add" "POST /api/v1/profiles/" '"silence_end_ms": 800' profiles add "$PROFILE" \
    --threshold intent=0.75 --vad silence_end_ms=800 --intent "$INTENT" --slot "$SLOT"
# get one profile
t "profiles get" "GET /api/v1/profiles/{profile_id}" "\"$INTENT\"" profiles get "$PROFILE"
# update the profile
t "profiles update" "PATCH /api/v1/profiles/{profile_id}" '"description": "smoke profile"' \
    profiles update "$PROFILE" --description "smoke profile"
# invalid VAD settings are rejected
tf "profiles update rejects bad VAD" "PATCH /api/v1/profiles/{profile_id}" 'silence_end_ms' \
    profiles update "$PROFILE" --vad silence_end_ms=1
# activate it; recognition now reports this profile
t "profiles activate" "POST /api/v1/profiles/{profile_id}/activate" '"is_active": true' profiles activate "$PROFILE"
t "test text uses active profile" "POST /api/v1/recognition/test_text" "\"profile_name\": \"$PROFILE\"" \
    test text "paint the wall blue" --no-save --verbose
# re-activate the profile that was active before
if [[ -n "$ORIG_PROFILE" ]]; then
    t "profiles activate original" "POST /api/v1/profiles/{profile_id}/activate" "\"name\": \"$ORIG_PROFILE\"" \
        profiles activate "$ORIG_PROFILE"
fi
# delete the profile
t "profiles remove" "DELETE /api/v1/profiles/{profile_id}" '"status": "ok"' profiles remove "$PROFILE"

# --- recognition ----------------------------------------------------------------------------------

# WAV file through wakeword + STT + intent (needs a Vosk model on the server)
if [[ $MODEL_OK == 1 && -f "$WAV" ]]; then
    t "test audio" "POST /api/v1/recognition/test_audio" '"intent_name"' test audio "$WAV" --require-wakeword
else
    skip "test audio" "POST /api/v1/recognition/test_audio" "no Vosk model on the server or WAV missing"
fi
# pipeline status
t "pipeline status" "GET /api/v1/recognition/status" '"running"' pipeline status
WAS_RUNNING="$(json_get 'd["running"]')"
# start the microphone pipeline
RE=1 t "pipeline start" "POST /api/v1/recognition/start" 'started|already_running' pipeline start
# stop it again (not if it was already running before this test)
if [[ "$WAS_RUNNING" == "False" ]]; then
    t "pipeline stop" "POST /api/v1/recognition/stop" '"status": "stopped"' pipeline stop
else
    skip "pipeline stop" "POST /api/v1/recognition/stop" "pipeline was already running"
fi
# SSE stream: open, then close after 2 s without events
t "test listen" "GET /api/v1/recognition/stream" '' test listen --no-start --duration 2
# SSE stream in debug mode: the first event is the pipeline status
RAW=1 t "test listen-debug" "GET /api/v1/recognition/stream" 'status:' test listen-debug --no-start --duration 2

# --- logs and performance --------------------------------------------------------------------------

# recent recognition logs
t "logs list" "GET /api/v1/logs/" '"normalized_transcript"' logs list --limit 5
LOG_ID="$(json_get 'd[0]["id"]')"
# one log entry
t "logs get" "GET /api/v1/logs/{log_id}" "\"id\": $LOG_ID" logs get "$LOG_ID"
# counts of matched / no_match
t "logs count" "GET /api/v1/logs/count" '"total"' logs count
# performance of the latest recognition
t "perf last" "GET /api/v1/performance/last" '"total_latency_ms"' perf last
# averages, percentiles and match rate
t "perf summary" "GET /api/v1/performance/summary" '"match_rate"' perf summary
# per-recognition performance rows
t "perf history" "GET /api/v1/performance/history" '"recognition_log_id"' perf history --limit 5

# --- failed utterances ----------------------------------------------------------------------------

# record a failure
t "failed add" "POST /api/v1/failed_utterances/" '"notes": "smoke"' failed add "tint the wall blue" --notes smoke
FU_ID="$(json_get 'd["id"]')"
# list unreviewed failures
t "failed list" "GET /api/v1/failed_utterances/" "\"id\": $FU_ID" failed list --unreviewed --limit 500
# one failure
t "failed get" "GET /api/v1/failed_utterances/{fu_id}" '"transcript": "tint the wall blue"' failed get "$FU_ID"
# mark reviewed
t "failed update" "PATCH /api/v1/failed_utterances/{fu_id}" '"reviewed": true' \
    failed update "$FU_ID" --reviewed --expected-intent "$INTENT"
# promote: slot value becomes a placeholder, and a regression test is created
t "failed promote" "POST /api/v1/failed_utterances/{fu_id}/promote" "tint the wall {$SLOT}" \
    failed promote "$FU_ID" --intent "$INTENT"
PROMOTED_REG="$(json_get 'd["regression_test"]["id"]')"
# the promoted phrase now matches with another value
t "test text after promote" "POST /api/v1/recognition/test_text" "\"intent_name\": \"$INTENT\"" \
    test text "tint the wall red" --no-save
# delete the failure
t "failed remove" "DELETE /api/v1/failed_utterances/{fu_id}" '"status": "ok"' failed remove "$FU_ID"

# --- regression tests -----------------------------------------------------------------------------

# add a regression test with an expected slot
t "regression add" "POST /api/v1/regression/" '"expected_slots"' \
    regression add "smoke_$TAG" "paint the wall blue" --expect-intent "$INTENT" --slot "$SLOT=blue"
REG_ID="$(json_get 'd["id"]')"
# list tests
t "regression list" "GET /api/v1/regression/" "\"id\": $REG_ID" regression list
# one test
t "regression get" "GET /api/v1/regression/{test_id}" "\"$SLOT\": \"blue\"" regression get "$REG_ID"
# rename it
t "regression update" "PATCH /api/v1/regression/{test_id}" "\"name\": \"smoke_${TAG}_renamed\"" \
    regression update "$REG_ID" --name "smoke_${TAG}_renamed"
# run all enabled tests (fails if any pre-existing regression test on the server fails too)
t "test regression" "POST /api/v1/regression/run" '"failed": 0' test regression
# delete this run's tests
t "regression remove" "DELETE /api/v1/regression/{test_id}" '"status": "ok"' regression remove "$REG_ID"
[[ -n "$PROMOTED_REG" && "$PROMOTED_REG" != "None" ]] && cli regression remove "$PROMOTED_REG" >/dev/null

# --- deletes --------------------------------------------------------------------------------------

# delete an alias
t "slots remove-alias" "DELETE /api/v1/slots/{slot_id}/values/{value_id}/aliases/{alias_id}" '"status": "ok"' \
    slots remove-alias "$SLOT" red crimson
# delete a value ('red' stays because the intent's phrases still use the slot)
t "slots remove-value" "DELETE /api/v1/slots/{slot_id}/values/{value_id}" '"status": "ok"' \
    slots remove-value "$SLOT" blue
# a slot used by a phrase cannot be deleted
tf "slots remove rejects slot in use" "DELETE /api/v1/slots/{slot_id}" "$SLOT" slots remove "$SLOT"
# delete the intent, then its slot, then the wakeword
t "intents remove" "DELETE /api/v1/intents/{intent_id}" '"status": "ok"' intents remove "$INTENT"
t "slots remove" "DELETE /api/v1/slots/{slot_id}" '"status": "ok"' slots remove "$SLOT"
t "wakewords remove" "DELETE /api/v1/wakewords/{wakeword_id}" '"status": "ok"' wakewords remove "$WW"

# --- configuration --------------------------------------------------------------------------------

# validate the server's config dir and database
t "config validate" "GET /api/v1/backup/validate" '"valid": true' config validate
# export the configuration as six YAML files
t "config export" "GET /api/v1/backup/export" '"status": "ok"' config export --out-dir "$TMP/export"
# validate local YAML files (uploaded as a bundle)
t "config validate --from-dir" "POST /api/v1/backup/validate" '"valid": true' \
    config validate --from-dir "$TMP/export"
# download the safety backup
t "config download" "GET /api/v1/backup/download/{filename}" '"path"' config download "$BACKUP" --out "$TMP"
# import the exported YAML (replaces configuration with the same content)
t "config import" "POST /api/v1/backup/import" '"status": "imported"' config import --from-dir "$TMP/export" --yes
# restore the safety backup: the server is back to the state before this run
t "config restore" "POST /api/v1/backup/restore/{filename}" '"status": "restored"' config restore "$BACKUP" --yes
# delete every backup created by this run (safety, pre-import, pre-restore)
cli config backups
for f in $(comm -13 <(echo "$BACKUPS_BEFORE") <(grep -oE 'opal_listener_[A-Za-z0-9_]+\.db' <<<"$OUT" | sort -u)); do
    t "config delete-backup $f" "DELETE /api/v1/backup/{filename}" '"status": "ok"' config delete-backup "$f" --yes
done

# --- coverage: every operation in the server's OpenAPI must have been tested ---------------------------

ALL_OPS="$(curl -fs "$API/openapi.json" | python3 -c '
import json, sys
spec = json.load(sys.stdin)
for path, item in spec["paths"].items():
    for method in item:
        print(f"{method.upper()} {path}")
')"
MISSING=()
while IFS= read -r op; do
    printf '%s\n' "${TESTED[@]}" | grep -qxF -- "$op" || MISSING+=("$op")
done <<<"$ALL_OPS"
TOTAL_OPS="$(grep -c . <<<"$ALL_OPS")"
if [[ ${#MISSING[@]} -eq 0 ]]; then
    report PASS "coverage: $TOTAL_OPS/$TOTAL_OPS REST operations tested"
else
    report FAIL "coverage: $((TOTAL_OPS - ${#MISSING[@]}))/$TOTAL_OPS REST operations tested" \
        "not tested: ${MISSING[*]}"
fi

# --- summary --------------------------------------------------------------------------------------

echo
echo "==== $PASSED passed, $FAILED failed, $SKIPPED skipped ($N tests)"
for f in "${FAILED_TESTS[@]}"; do echo "  FAIL $f"; done
[[ $FAILED -eq 0 ]]
