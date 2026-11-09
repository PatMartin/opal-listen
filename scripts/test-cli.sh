#/usr/bin/bash

clear
echo "Testing WAKEWORDS..."
#opal --help
#opal listen --help
#opal listen wakeword --help
#opal listen wakeword list
#opal listen wakeword get --help
#opal listen wakeword get computer
#opal listen wakeword add --help
#opal listen wakeword add nabu --threshold 0.7 --disabled
#opal listen wakeword get nabu
#opal listen wakeword update --help
#opal listen wakeword update nabu --threshold 0.95 --enabled
#opal listen wakeword get nabu
#opal listen wakeword list
#opal listen wakeword remove --help
#opal listen wakeword remove nabu
#opal listen wakeword list
echo "Testing INTENTS..."
#opal listen intent --help
#opal listen intent list --help
#opal listen intent list
#opal listen intent get --help
#opal listen intent get get_time
#opal listen intent add --help
opal listen intent add test_intent --phrase "Test intent one" --phrase "Test intent two" --description "This is a test intent"
#opal listen intent list
#opal listen intent update --help
#opal listen intent update test_intent --description "Updated description"
#opal listen intent list
#opal listen intent remove --help
#opal listen intent list

echo "Testing INTENT PHRASES..."
#opal listen intent phrases --help
#opal listen intent phrases test_intent
#opal listen intent remove-phrase --help
#opal listen intent remove-phrase test_intent "Test intent one"
#opal listen intent phrases test_intent

echo "Testing SLOTS..."
#opal listen slot --help
#opal listen slot list --help
#opal listen slot list
#opal listen slot get --help
#opal listen slot get temperature
#opal listen slot add --help
#opal listen slot add test_slot --type number --value 25 --description "Test slot"
#opal listen slot list
#opal listen slot get test_slot
#opal listen slot update --help
#opal listen slot update test_slot --description "Updated test slot"
#opal listen slot get test_slot
#opal listen slot values --help
#opal listen slot values room_name
#opal listen slot add-value --help
#opal listen slot values test_slot
#opal listen slot add-value test_slot 42
#opal listen slot values test_slot
#opal listen slot remove-value --help
#opal listen slot remove-value test_slot 42
#opal listen slot values test_slot
#opal listen slot aliases --help
#opal listen slot aliases room_name living_room
#opal listen slot add-alias --help
#opal listen slot add-alias room_name living_room martin_hall
#opal listen slot aliases room_name living_room
#opal listen slot remove-alias --help
#opal listen slot remove-alias room_name living_room martin_hall
#opal listen slot aliases room_name living_room

#opal listen intent list
opal listen intent remove test_intent
#opal listen intent list

#opal listen slot remove --help
#opal listen slot list
#opal listen slot remove test_slot
#opal listen slot list

echo "Testing PROFILES..."
#opal listen profile --help
#opal listen profile list --help
#opal listen profile list
#opal listen profile get --help
#opal listen profile get 4
#opal listen profile add --help
#opal listen --json profiles add test_profile \
#  --description "Test Profile" \
#  --wakeword nabu \
#  --threshold wakeword=0.85 --threshold intent=0.8 --threshold stt=0.6 \
#  --vad energy_threshold_dbfs=-35 --vad noise_margin_db=12 \
#  --vad silence_end_ms=900 --vad max_utterance_ms=10000 --vad use_stt_endpoint=false \
#  --intent cancel --intent set_temperature --intent get_time --intent cancel \#
#  --slot temperature
#opal listen profile update test_profile --description "Updated Test Profile"
#opal listen profile activate test_profile
#opal listen profile remove --help
#opal listen profile list
#opal listen profile remove test_profile
#opal listen profile list

echo "Testing SETTINGS..."
#opal listen setting --help
#opal listen setting list
#opal listen setting get --help
#opal listen setting get whisper_model
#opal listen setting set whisper_model medium.en
#opal listen setting set whisper_model tiny.en
#opal listen setting reset whisper_model
#opal listen setting list

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
#opal listen log --help
#opal listen log list --help
#opal listen log get --help
#opal listen log count --help

echo "Testing PERF..."
#opal listen service perf --help
#opal listen service service perf last --help
#opal listen service service perf summary --help
#opal listen service service perf history --help

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
opal listen service --help
opal listen service doctor
opal listen service devices
opal listen service info
opal listen service ping
