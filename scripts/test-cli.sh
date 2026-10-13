#/usr/bin/bash

clear
echo "Testing WAKEWORDS..."
#opal --help
#opal listen --help
#opal listen wakewords --help
#opal listen wakewords list
#opal listen wakewords get --help
#opal listen wakewords get computer
#opal listen wakewords add --help
#opal listen wakewords add nabu --threshold 0.7 --disabled
#opal listen wakewords get nabu
#opal listen wakewords update --help
#opal listen wakewords update nabu --threshold 0.95 --enabled
#opal listen wakewords get nabu
#opal listen wakewords list
#opal listen wakewords remove --help
#opal listen wakewords remove nabu
#opal listen wakewords list
echo "Testing INTENTS..."
#opal listen intents --help
#opal listen intents list --help
#opal listen intents list
#opal listen intents get --help
#opal listen intents get get_time
#opal listen intents add --help
opal listen intents add test_intent --phrase "Test intent one" --phrase "Test intent two" --description "This is a test intent"
#opal listen intents list
#opal listen intents update --help
#opal listen intents update test_intent --description "Updated description"
#opal listen intents list
#opal listen intents remove --help
#opal listen intents list

echo "Testing INTENT PHRASES..."
#opal listen intents phrases --help
#opal listen intents phrases test_intent
#opal listen intents remove-phrase --help
#opal listen intents remove-phrase test_intent "Test intent one"
#opal listen intents phrases test_intent

echo "Testing SLOTS..."
#opal listen slots --help
#opal listen slots list --help
#opal listen slots list
#opal listen slots get --help
#opal listen slots get temperature
#opal listen slots add --help
#opal listen slots add test_slot --type number --value 25 --description "Test slot"
#opal listen slots list
#opal listen slots get test_slot
#opal listen slots update --help
#opal listen slots update test_slot --description "Updated test slot"
#opal listen slots get test_slot
#opal listen slots values --help
#opal listen slots values room_name
#opal listen slots add-value --help
#opal listen slots values test_slot
#opal listen slots add-value test_slot 42
#opal listen slots values test_slot
#opal listen slots remove-value --help
#opal listen slots remove-value test_slot 42
#opal listen slots values test_slot
#opal listen slots aliases --help
#opal listen slots aliases room_name living_room
#opal listen slots add-alias --help
#opal listen slots add-alias room_name living_room martin_hall
#opal listen slots aliases room_name living_room
#opal listen slots remove-alias --help
#opal listen slots remove-alias room_name living_room martin_hall
#opal listen slots aliases room_name living_room

#opal listen intents list
opal listen intents remove test_intent
#opal listen intents list

#opal listen slots remove --help
#opal listen slots list
#opal listen slots remove test_slot
#opal listen slots list

echo "Testing PROFILES..."
#opal listen profiles --help
#opal listen profiles list --help
#opal listen profiles list
#opal listen profiles get --help
#opal listen profiles get 4
#opal listen profiles add --help
#opal listen --json profiles add test_profile \
#  --description "Test Profile" \
#  --wakeword nabu \
#  --threshold wakeword=0.85 --threshold intent=0.8 --threshold stt=0.6 \
#  --vad energy_threshold_dbfs=-35 --vad noise_margin_db=12 \
#  --vad silence_end_ms=900 --vad max_utterance_ms=10000 --vad use_stt_endpoint=false \
#  --intent cancel --intent set_temperature --intent get_time --intent cancel \#
#  --slot temperature
#opal listen profiles update test_profile --description "Updated Test Profile"
#opal listen profiles activate test_profile
#opal listen profiles remove --help
#opal listen profiles list
#opal listen profiles remove test_profile
#opal listen profiles list

echo "Testing SETTINGS..."
#opal listen settings --help
#opal listen settings list
#opal listen settings get --help
#opal listen settings get whisper_model
#opal listen settings set whisper_model medium.en
#opal listen settings set whisper_model tiny.en
#opal listen settings reset whisper_model
#opal listen settings list

echo "Testing TEST..."
#opal listen test --help
#opal listen test text "computer, turn on the lights"
#opal listen test audio "resources/cmds/cmd-kitchen-lights-off.wav"
#opal listen test listen --help
#opal listen test listen-debug --help
#opal listen test listen-debug --count 10 --duration 10 --start
#opal listen test regression --help
#opal listen test regression

echo "Testing PIPELINE..."
#opal listen pipeline --help
#opal listen pipeline status
#opal listen pipeline start
#opal listen pipeline status
#opal listen pipeline stop
#opal listen pipeline status

echo "Testing FAILED..."
#opal listen failed --help
#opal listen failed list --help
#opal listen failed get --help 
#opal listen failed add --help 
#opal listen failed update --help 
#opal listen failed remove --help 
#opal listen failed promote --help 

echo "Testing REGRESSION..."
#opal listen regression --help
#opal listen regression list --help
#opal listen regression get --help
#opal listen regression add --help
#opal listen regression update --help
#opal listen regression remove --help

echo "Testing LOGS..."
#opal listen logs --help
#opal listen logs list --help
#opal listen logs get --help
#opal listen logs count --help

echo "Testing PERF..."
#opal listen perf --help
#opal listen perf last --help
#opal listen perf summary --help
#opal listen perf history --help

echo "Testing CONFIG..."
#opal listen config --help
#opal listen config validate --help
#opal listen config backup --help
#opal listen config restore --help
#opal listen config download --help
#opal listen config delete-backup --help
#opal listen config export --help
#opal listen config import --help

echo "Testing SYSTEM..."
opal listen system --help
opal listen system doctor
opal listen system devices
opal listen system info
opal listen system ping
