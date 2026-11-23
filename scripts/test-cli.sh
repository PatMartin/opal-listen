#!/usr/bin/env bash
# test-cli.sh - exercise every `opal listen` command against a managed server.
#
# Not a pass/fail suite (see ../test-cli.sh for that): this runs each command once so the
# output can be eyeballed. State created by the run (test_slot, test_intent, test_profile,
# test_reaction, failed rows) is removed again at the end.
#
# Notes:
#   - `reaction` commands edit <config_dir>/reactions.yaml directly (that is how arming
#     works); the test reaction is removed at the end.
#   - `test audio` needs a Vosk model on the server; `pipeline start` needs a microphone.
#   - `service health` exits 1 while any check reports an error - the script keeps going.

clear
opal listen service stop
opal listen service start

echo "Testing WAKEWORDS..."
#opal listen wakeword --help
opal listen wakeword list
opal listen wakeword add opal
opal listen wakeword add computer
#opal listen wakeword show --help
opal listen wakeword show computer
opal listen wakeword show opal
#opal listen wakeword add --help
opal listen wakeword add nabu --threshold 0.7 --disabled
opal listen wakeword show nabu
#opal listen wakeword update --help
opal listen wakeword update nabu --threshold 0.95 --enabled
opal listen wakeword show nabu
opal listen wakeword list
#opal listen wakeword remove --help
opal listen wakeword remove nabu
opal listen wakeword list

echo "Testing SLOTS..."
#opal listen slot --help
opal listen slot list
#opal listen slot add --help
opal listen slot add test_slot --type list --value blue --value red --description "Test slot"
opal listen slot list
#opal listen slot show --help
opal listen slot show test_slot
#opal listen slot update --help
opal listen slot update test_slot --description "Updated test slot"
opal listen slot show test_slot
#opal listen slot value list --help
opal listen slot value list test_slot
#opal listen slot value add --help
opal listen slot value add test_slot green --alias emerald
opal listen slot value list test_slot
#opal listen slot aliases --help
opal listen slot aliases test_slot green
#opal listen slot add-alias --help
opal listen slot add-alias test_slot blue azure
opal listen slot aliases test_slot blue
#opal listen slot remove-alias --help
opal listen slot remove-alias test_slot blue azure
opal listen slot aliases test_slot blue
#opal listen slot value remove --help
opal listen slot value remove test_slot red
opal listen slot value list test_slot

echo "Testing INTENTS..."
#opal listen intent --help
opal listen intent list
#opal listen intent add --help
opal listen intent add test_intent \
    --phrase "Test intent one" --phrase "paint the wall {test_slot}" \
    --description "This is a test intent"
opal listen intent list
#opal listen intent show --help
opal listen intent show test_intent
#opal listen intent update --help
opal listen intent update test_intent --description "Updated description"
opal listen intent show test_intent

echo "Testing INTENT PHRASES..."
#opal listen phrase list --help
opal listen phrase list test_intent
#opal listen phrase add --help
opal listen phrase add test_intent "Test intent three"
opal listen phrase list test_intent
#opal listen phrase remove --help
opal listen phrase remove test_intent "Test intent three"
opal listen phrase list test_intent

echo "Testing PROFILES..."
#opal listen profile --help
opal listen profile list
ACTIVE_PROFILE=$(opal listen --json profile list \
    | python3 -c 'import json,sys; print(next((p["name"] for p in json.load(sys.stdin) if p["is_active"]), ""))')
#opal listen profile add --help
opal listen profile add test_profile --description "Test Profile" \
    --wakeword opal \
    --threshold wakeword=0.85 --threshold intent=0.8 \
    --vad silence_end_ms=900 --vad max_utterance_ms=10000 \
    --intent test_intent --slot test_slot
opal listen profile list
#opal listen profile show --help
opal listen profile show test_profile
#opal listen profile update --help
opal listen profile update test_profile --description "Updated Test Profile"
opal listen profile show test_profile
#opal listen profile activate --help
if [[ -n "$ACTIVE_PROFILE" ]]; then
    opal listen profile activate test_profile
    opal listen profile list
    # restore the previously active profile, then drop the test profile
    opal listen profile activate "$ACTIVE_PROFILE"
else
    # nothing to restore: the active profile cannot be deleted, so leave test_profile inactive
    echo "(skipping profile activate: no active profile to restore)"
fi
#opal listen profile remove --help
opal listen profile remove test_profile
opal listen profile list

echo "Testing SETTINGS..."
#opal listen config setting --help
opal listen config setting list
#opal listen config setting show --help
opal listen config setting show --name cooldown_ms
#opal listen config setting set --help
opal listen config setting set --name cooldown_ms --value 1234
opal listen config setting show --name cooldown_ms
#opal listen config setting remove --help (reverts the key to its default)
opal listen config setting remove --name cooldown_ms
opal listen config setting show --name cooldown_ms

echo "Testing REACTIONS... (edits reactions.yaml; test_reaction is removed at the end)"
#opal listen reaction --help
opal listen reaction path
opal listen reaction list
#opal listen reaction add --help
opal listen reaction add test_reaction \
    --cond "intent_name eq test_intent" --run "echo test_reaction fired"
opal listen reaction list
#opal listen reaction show --help
opal listen reaction show test_reaction
#opal listen test reaction --help (dry-run; --execute would really run the commands)
opal listen test reaction --payload '{"intent_name": "test_intent"}'
opal listen test reaction --payload '{"intent_name": "other"}'
opal listen test reaction --text "paint the wall blue"
#opal listen reaction update --help
opal listen reaction update test_reaction --cond "intent_name in [test_intent, get_time]"
opal listen reaction disable test_reaction
opal listen test reaction --payload '{"intent_name": "test_intent"}'
opal listen reaction enable test_reaction
#opal listen reaction log --help
opal listen reaction log
#opal listen reaction remove --help
opal listen reaction remove test_reaction
opal listen reaction list

echo "Testing TEST..."
#opal listen test --help
#opal listen test text --help
opal listen test text "paint the wall blue" --expect-intent test_intent
opal listen test text "this matches nothing" --no-save
#opal listen test audio --help (needs a Vosk model on the server)
opal listen test audio "resources/cmds/cmd-kitchen-lights-off.wav"
#opal listen test listen --help (SSE stream; --no-start so the mic is not required)
opal listen test listen --no-start --duration 2

echo "Testing PIPELINE..."
#opal listen pipeline --help
opal listen pipeline status
#opal listen pipeline start --help (needs a microphone)
opal listen pipeline start
opal listen pipeline status
#opal listen pipeline stop --help
opal listen pipeline stop
opal listen pipeline status

echo "Testing FAILED..."
#opal listen failed --help
opal listen failed list
#opal listen failed add --help
FU_ID=$(opal listen --json failed add "tint the wall green" --notes smoke \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
opal listen failed list --unreviewed
#opal listen failed show --help
opal listen failed show "$FU_ID"
#opal listen failed update --help
opal listen failed update "$FU_ID" --reviewed --expected-intent test_intent
opal listen failed show "$FU_ID"
opal listen phrase add test_intent "tint the wall {test_slot}"
opal listen phrase list test_intent
opal listen test text "tint the wall blue" --expect-intent test_intent --no-save
#opal listen failed remove --help
opal listen failed remove "$FU_ID"

echo "Testing PERF..."
#opal listen service perf --help
opal listen service perf last
opal listen service perf summary
opal listen service perf history --limit 10

echo "Testing CONFIG..."
#opal listen config --help
#opal listen config validate --help
opal listen config validate
#opal listen config save --help (--out writes a snapshot file; without it the managed
#  snapshot on the server is replaced)
opal listen config save --out /tmp/test-cli-snapshot.json
opal listen config validate --in /tmp/test-cli-snapshot.json
opal listen config save
#opal listen config load --help
opal listen config load --in /tmp/test-cli-snapshot.json --yes
opal listen config load --yes

echo "Testing SYSTEM..."
#opal listen info --help
opal listen service health
#opal listen service --help
#opal listen service log --help
opal listen service log --lines 10
#opal listen tree --help
opal listen tree --ascii
#opal listen gui --help (--print shows the URL instead of opening a browser)
opal listen gui --print

echo "Cleaning up..."
# the added phrase and the intent must go before their slot can be removed
opal listen phrase remove test_intent "tint the wall {test_slot}"
opal listen intent remove test_intent
opal listen slot remove test_slot
opal listen intent list
opal listen slot list

opal listen service stop
