# OPAL Listen Cookbook

Forty recipes for `opal-listen` alone, then across the whole OPAL suite.

**How to use the snippets:** every recipe is one fenced code block — hover it on
GitHub (or any CommonMark renderer) and click the copy icon that appears, then
paste straight into your shell. Multi-step recipes paste as a single unit.

**Conventions used below**

- `opal listen ...` talks to the listener REST API (default `http://localhost:8000`;
  override with `opal listen --api-url URL ...` or `OPAL_LISTEN_API_URL`).
- Ports used in Part 2: listen `:8000`, talk `:8001`, look `:8002`
  (`OPAL_LOOK_PORT=8002`), event `:8080`, think `:8082` (`OPAL_THINK_PORT=8082`).
- IDs shown (fu `7`, rule `say_result`, snapshot paths) are examples — substitute
  what your service prints.
- Most mutations auto-fetch the resource ETag and send `If-Match` for you; if the
  server answers `412`, someone else changed it — rerun to re-fetch.
- Global flags (`--json`, `--api-url`) go right after `listen`:
  `opal listen --json service perf summary`, not `opal listen service perf summary --json`.
- Names of intents, slots and profiles must be `snake_case`.
- Part 1 builds on the seeded catalog (intents `light_turn_on`, `get_time`,
  `cancel`…, slot `room_name`) — run the recipes in order on a fresh install.
- Run `opal listen tree` any time for a live map of every command and flag below.

---

## Part 1 — twenty things you can only do with opal-listen

### 1. Prototype a voice command without touching a microphone

`test text` runs your words through the same normalizer, filler-word stripper,
intent matcher and slot extractor the live pipeline uses — no audio needed. With
`--expect-intent` it exits 1 on a miss, so it doubles as a unit test.

```bash
opal listen intent add lights_on --description "Switch a room's lights on" \
  --phrase "turn on the lights" --phrase "lights on" --phrase "switch the lights on"
opal listen test text "computer, please turn on the lights" --expect-intent lights_on
```

### 2. Extract structured slots from free speech

Phrases embed `{slot_name}` placeholders (and `[optional]` words). List slots take
canonical values; aliases map how people actually talk onto those values. The
seeded `light_turn_on` intent already uses `{room_name}` — teach it a new
nickname and the slot comes back canonical.

```bash
opal listen slot values room_name
opal listen slot add-alias room_name office "the den"
opal listen test text "computer, turn on the den light" --expect-intent light_turn_on --verbose
# -> "slot_values": {"room_name": "office"}
```

Before adding a *second* intent with overlapping phrases, run
`opal listen --json config validate` — it warns when two phrases are ambiguous.
On a tie the earlier intent wins.

### 3. Give the assistant more than one wake word

Wakewords are first-class catalog rows — add a second trigger name, tune its
confidence independently, and both work immediately in the live pipeline.

```bash
opal listen wakeword add jarvis --threshold 0.85
opal listen wakeword list
opal listen test text "jarvis, turn on the lights" --expect-intent lights_on
```

### 4. De-noise a trigger-happy wakeword

Two levers, both hot-applied: raise the wakeword's own threshold, and add
sound-alike *distractors* (comma-separated words whose transcript presence vetoes
the hit). Then watch the no-match side of the log stream to confirm.

```bash
opal listen wakeword update computer --threshold 0.9
opal listen config setting set --name wakeword_distractors --value "commuter,compute,computing,commute"
opal listen failed list
```

### 5. Batch-test a folder of golden WAVs

`test audio` feeds a real 16-bit PCM file through wakeword detection, STT and
intent matching end-to-end. `--require-wakeword` makes a file score as `no_match`
unless it actually opens with the wakeword — perfect for negative samples too.

```bash
for f in samples/positives/*.wav; do
  opal listen test audio "$f" --require-wakeword --expect-intent lights_on || echo "FAIL: $f"
done
opal listen test audio samples/negatives/smalltalk.wav --require-wakeword --verbose
```

### 6. Open a live sound-engineer console

`test listen --debug` streams every SSE event: a real-time dBFS level meter, partial
transcripts, VAD and wakeword events, then the final result. The fastest way to
answer "did it even hear me?".

```bash
opal listen test listen --debug --duration 60
```

### 7. Run a local script when a phrase matches — no event bus

Reactions turn a matched intent into a shell command, inside opal-listen itself:
no broker, no second service. Commands live only in `reactions.yaml` (the CLI
edits that file locally; the REST API can never arm one). The script gets the
full match result as JSON on stdin, and edits hot-reload.

```bash
cat > /opt/opal/lights.sh <<'SH'
#!/usr/bin/env bash
room=$(jq -r '.slot_values.room_name // "everywhere"')   # stdin = match JSON
logger -t opal "lights on: $room"                        # swap in your real actuator
SH
chmod +x /opt/opal/lights.sh
opal listen reaction add lights \
  --cond "intent_name eq light_turn_on" \
  --cond "intent_confidence gte 0.8" \
  --run "/opt/opal/lights.sh" --timeout 10
opal listen config setting set --name reactions_enabled --value true   # master switch, off by default
opal listen test listen --count 1                 # say: "computer, turn on the kitchen light"
opal listen reaction log --limit 5
```

Only live-microphone matches fire. `test text`/`test audio` never do, so
diagnostics can't trigger side effects. Repeats inside `dedup_window_ms` are
suppressed as well.

### 8. Put a phrase gate in front of catalog edits

Keep the phrases that matter in a file and make `test text --expect-intent` a
hard gate — it exits 1 when a phrase resolves to a different intent, before and
after every vocabulary change.

```bash
while IFS='|' read -r phrase intent; do
  opal listen test text "$phrase" --expect-intent "$intent" --no-save
done <<'EOF'
turn on the kitchen light|light_turn_on
turn on the den light|light_turn_on
order a pizza|no_match
EOF
```

### 9. Wire the service into CI, systemd and Kubernetes

Four probe shapes for four depths of "is it healthy": `ping` (TCP/HTTP up),
`live` (process + DB), `ready` (config compiles, provider loads), `doctor`
(every startup check, human-readable). All exit non-zero on failure.

```bash
opal listen service live && opal listen service live && opal listen service ready
opal listen info health   # ✓/!/✗ per check: sqlite, model, microphone, intents, slots
```

### 10. Retune several settings

`config setting set` applies one key per call, validated server-side with an
`If-Match` precondition — a rejected value leaves the stored setting untouched.
Verify what a key actually controls with `config setting show` first.

```bash
opal listen config setting show --name cooldown_ms
opal listen config setting set --name cooldown_ms --value 800
opal listen config setting set --name dedup_window_ms --value 4000
opal listen config setting set --name filler_words --value "uh,um,er,please"
opal listen config setting list
```

### 11. Snapshot the catalog before surgery — and roll back

`config save` writes a portable snapshot of the desired configuration — managed
server-side, or to a local file with `--out`. `config load` validates first and
applies atomically, so a bad load leaves the previous configuration untouched;
re-loading your saved snapshot rolls everything back.

```bash
opal listen config save --out ./before-surgery.json
# ... make changes ...
opal listen config load --in ./before-surgery.json --yes
```

### 12. Keep your whole voice model in git

`config save --out` writes one JSON snapshot envelope (wakewords, intents, slots,
profiles, personas, settings) as reviewable source. Validate
offline, then load — which validates again and applies atomically.

```bash
opal listen config save --out ./voice-config/snapshot.json
git add voice-config && git commit -m "voice model snapshot"
opal listen config validate --in ./voice-config/snapshot.json
opal listen config load --in ./voice-config/snapshot.json --yes
```

### 13. One service, two acoustic environments

Profiles bundle wakeword selection, per-stage thresholds, VAD timing, enabled
intents/slots and model paths. Switching is instant and atomic — only one
profile is active at a time.

```bash
opal listen profile add noisy_kitchen \
  --threshold wakeword=0.9 --threshold intent=0.85 --vad silence_end_ms=900
opal listen profile add quiet_study \
  --threshold wakeword=0.7 --vad silence_end_ms=400
opal listen profile activate noisy_kitchen
opal listen profile list
```

### 14. A night-watch profile that hears only alarms

`--intent` narrows a profile to an explicit intent allowlist — after dark, the
house only answers to two commands. Switch back at dawn by re-activating the
default profile.

```bash
opal listen intent add alarm_stop --phrase "stop the alarm" --phrase "alarm off"
opal listen profile add night_watch --intent alarm_stop --intent cancel
opal listen profile activate night_watch
opal listen test text "computer, turn on the kitchen light"   # NO MATCH after dark
opal listen test text "computer, stop the alarm"              # MATCH
# morning:
opal listen profile activate default
```

### 15. Turn "it didn't understand me" into training data

Users report misses; you log them, review, and `failed promote` writes the
corrected transcript into the right intent as a generalized training phrase.

```bash
opal listen failed add "light on in the kitchen" --expected-intent light_turn_on \
  --notes "field report: quiet voice at distance"
opal listen failed list --unreviewed
opal listen failed promote 7 --intent light_turn_on
# -> promoted as 'light on in the {room_name}' (slot values are generalized)
```

### 16. Mine the recognition history for what people really say

Every recognition is stored (source, transcript, intent, slots, confidences, latency,
no-match reason). v2.0.0 removed the `log` read API; `service perf` gives the
per-recognition metrics and `failed` the captured no_match utterances — filter by
intent or match status there to find where the model leaks.

```bash
opal listen service perf summary
opal listen service perf history --limit 50
opal listen failed list
opal listen failed get 424
```

### 17. Audit your wake-to-intent latency budget

`service perf` rows break each recognition into wakeword / STT / intent-stage
milliseconds. `summary` gives avg/p50/p95/max so you can tell whether "it feels
slow" is the wakeword stage or the STT stage.

```bash
opal listen service perf last
opal listen service perf summary --limit 200
opal listen service perf history --limit 25
```

### 18. Rehearse a reaction before it can fire

`reaction test` evaluates every armed reaction against a result without
running anything, and names the condition that failed. `--text` gets the payload
from a real recognition, so you test exactly what the live pipeline will
produce. Add `--execute` only when you want to debug the script itself.

```bash
opal listen reaction list
opal listen reaction test --text "computer, turn on the den light"   # ✓ lights — would fire
opal listen reaction test --text "computer, what time is it"         # ✗ (intent_name eq 'light_turn_on')
opal listen reaction test --payload \
  '{"intent_name":"light_turn_on","slot_values":{"room_name":"office"},"intent_confidence":0.9}' --execute
opal listen reaction disable lights                  # park it; keeps conditions and actions
opal listen config setting set --name reactions_enabled --value false      # or stop all reactions at once
```

Conditions on a field that isn't in the payload never match. Include every field
your conditions use, such as `intent_confidence` above, or test for presence with
`--cond "slot_values.room_name exists"`.

### 19. Hot-swap the recognition engine — no restart

Everything in the settings catalog applies live. Flip grammar-constrained STT to
open dictation, bolt on the faster-whisper refiner for a second pass, and roll it
back with `unset` — all while the pipeline keeps running.

```bash
opal listen config setting set --name stt_mode --value open
opal listen config setting set --name stt_refiner --value faster_whisper
opal listen config setting set --name whisper_model --value base.en
opal listen test text "computer, what is the airspeed of an unladen swallow"
# back out:
opal listen config setting remove --name stt_refiner && opal listen config setting set --name stt_mode --value grammar
```

### 20. Harden a headless appliance

Pick the right mic by name, let the watchdog recover dead captures, and bound the
log table — all without editing a file or restarting.

```bash
opal listen info health
opal listen config setting set --name audio_device --value "USB"
opal listen config setting set --name audio_watchdog_timeout_ms --value 5000
opal listen config setting set --name max_recognition_logs --value 5000
opal listen config setting set --name auto_start_listener --value true
```

---

## Part 2 — twenty things you can only do with the whole OPAL suite

Each module is small on purpose; the interesting behavior lives in the wiring.
opal-listen hears, opal-event routes (`topic` + `conditions` + `actions`),
opal-look sees, opal-think reasons (only through registered event actions), and
opal-talk speaks. `opal-api` holds the contract-of-record for every service.

**Reactions or opal-event?** For "this phrase runs that script on this box",
listen's built-in reactions (recipes 7 and 18) are enough. Use opal-event when
you need HTTP or Python actions, rules over events from *other* producers (look,
think), fan-out to many actions, or a central execution history. The
two share a condition grammar; listen's `intent_name` becomes `data.intent_name`
in a bus event. Recipes below enable event publication with the legacy flag
(`event_publication_enabled true`), which keeps working because the default
`intent_dispatch=auto` resolves from the legacy flags. To be explicit, set the
policy instead: `opal listen config setting set --name intent_dispatch --value event`
(or `both` for event bus + local reactions). Each result's `dispatched_to`
field reports which path was selected.

### 21. "OK" — the closed voice loop

The flagship recipe: speech → intent → bus event → spoken reply. Listen
publishes `opal.listen.intent.match` on every match; an opal-event rule hands
the event to a shell action that turns it into words through opal-talk.

```bash
opal listen config setting set --name event_publication_enabled --value true
cat > /opt/opal/speak_ack.sh <<'SH'
#!/usr/bin/env bash
event=$(cat)   # stdin carries the event JSON (stdin: "event_json")
intent=$(jq -r '.data.intent_name // "unknown"' <<<"$event")
slots=$(jq -r '.data.slot_values // {} | to_entries | map("\(.key) \(.value)") | join(", ")' <<<"$event")
opal talk generate "OK: ${intent}${slots:+ with ${slots}}" --stream -o - | aplay -r 22050 -f S16_LE -c 1
SH
chmod +x /opt/opal/speak_ack.sh
opal event actions create --json \
  '{"id":"speak_ack","type":"shell","mode":"bash","script_path":"/opt/opal/speak_ack.sh","timeout_seconds":15}'
opal event rules create --json \
  '{"id":"ack_intents","enabled":true,"topic":"opal.listen.intent.match","conditions":[],"actions":["speak_ack"]}'
opal listen test listen --count 1
```

For a hands-off verification of this whole loop, `scripts/event_say_smoke.py`
spins up real opal-event and opal-say instances, replays a checked-in WAV
fixture through the live capture path, and asserts the matched intent, the
stored event ID, the action's execution receipt, and a nonempty synthesized
WAV:

```bash
.venv/Scripts/python.exe scripts/event_say_smoke.py   # needs ../opal-event + ../opal-say checkouts
```

### 22. Dry-fire the loop without a microphone

`opal listen event publish` writes straight onto the ZeroMQ bus — inject a
synthetic `opal.listen.intent.match` envelope and watch the rule fire, no
wakeword required. Then audit the action run.

```bash
opal listen event publish opal.listen.intent.match \
  '{"intent_name":"light_turn_on","slot_values":{"room_name":"kitchen"},"source":"cli-test"}'
opal event executions list
opal event list --topic opal.listen.intent.match --limit 5
```

### 23. "Opal, what do you see?" — spoken scene description

A voice intent asks the question; a shell action captures an annotated frame
through opal-look, reads the observation summary, and speaks it back. Vision
query latency stays out of the audio path until asked.

```bash
opal listen intent add describe_scene --phrase "what do you see" --phrase "describe the scene"
opal listen config setting set --name event_publication_enabled --value true
cat > /opt/opal/describe.sh <<'SH'
#!/usr/bin/env bash
labels=$(opal-look-cli analyze run --body '{"source":{"source_id":"doorcam"},"task":"detection"}' \
         | jq -r '[.. | .label? // empty] | unique | join(", ")')
opal talk generate "I see: ${labels:-nothing right now}" --stream -o - | aplay -r 22050 -f S16_LE -c 1
SH
chmod +x /opt/opal/describe.sh
opal event actions create --json \
  '{"id":"describe_scene","type":"shell","mode":"bash","script_path":"/opt/opal/describe.sh","timeout_seconds":60}'
opal event rules create --json \
  '{"id":"voice_to_look","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"describe_scene"}],"actions":["describe_scene"]}'
```

### 24. "Opal, ask the brain" — voice in, reasoning out

`opal think` only acts through registered event actions — so this recipe is safe
by construction: the model can *say* things but cannot *do* anything you didn't
register. Voice prompt goes in; the answer is spoken by opal-talk.

```bash
opal listen slot add question --type text        # slot must exist before a phrase uses it
opal listen intent add ask_brain --phrase "ask the brain {question}"
opal listen test text "computer, ask the brain why is the sky blue"   # question = "why is the sky blue"
opal listen config setting set --name event_publication_enabled --value true
cat > /opt/opal/ask_brain.sh <<'SH'
#!/usr/bin/env bash
q=$(jq -r '.data.slot_values.question // "status report"' </dev/stdin)
opal think ask "Answer briefly for a voice reply: $q" --no-stream \
  | opal talk generate - --stream -o - | aplay -r 22050 -f S16_LE -c 1
SH
chmod +x /opt/opal/ask_brain.sh
opal event actions create --json \
  '{"id":"ask_brain","type":"shell","mode":"bash","script_path":"/opt/opal/ask_brain.sh","timeout_seconds":90}'
opal event rules create --json \
  '{"id":"voice_to_brain","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"ask_brain"}],"actions":["ask_brain"]}'
```

### 25. Presence greeting — eyes open the mouth

The reverse direction: an opal-look monitor spots a person and publishes its own
event; opal-event routes it to a talk action. Listen isn't involved at all —
proof the bus composes any producer with any consumer.

```bash
opal event actions create --json \
  '{"id":"greet","type":"shell","mode":"exec","executable":"/usr/bin/opal","args":["talk","generate","Hello, I see you.","--stream","-o","-"],"stdin":"none","timeout_seconds":15}'
opal event rules create --json \
  '{"id":"greet_person","topic":"opal.look.observation","conditions":[{"path":"data.labels","operator":"contains","value":"person"}],"actions":["greet"]}'
opal-look-cli monitors stream front-door
```

### 26. Trace one utterance across three services

Every OPAL service stamps the same `X-Opal-*` correlation headers. Find the
recognition row in listen's logs, the rule firing in event's executions, and the
speech request in talk — one utterance, one audit trail.

```bash
opal listen --json failed list
opal event executions list --limit 10
opal event get <event-id from the execution>
opal listen failed get <failed-utterance-id>   # expected intent, notes, review state
```

### 27. "Good night" — one phrase, a house-wide scene

One intent, one rule, many actions: opal-event fans a single event out to a talk
announcement and an HTTP action arming the camera monitor. Rules execute their
action list together.

```bash
opal listen intent add good_night --phrase "good night" --phrase "bedtime"
opal listen config setting set --name event_publication_enabled --value true
opal event actions create --json \
  '{"id":"night_tts","type":"http","method":"POST","url":"http://127.0.0.1:8001/api/v1/generate","body_mode":"template","body":{"text":"Good night. Locking up."},"timeout_seconds":10}'
opal event actions create --json \
  '{"id":"arm_doorcam","type":"http","method":"POST","url":"http://127.0.0.1:8002/api/v1/monitors/front-door","body_mode":"template","body":{"enabled":true},"timeout_seconds":10}'
opal event rules create --json \
  '{"id":"good_night","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"good_night"}],"actions":["night_tts","arm_doorcam"]}'
```

### 28. Approve dangerous intents with a second wakeword check

Chain rules on conditions: `unlock_door` first fires a *confirmation prompt*
action; only a follow-up `confirm_dangerous` intent event (with a `confirm=yes`
condition) runs the real actuator. Two spoken confirmations, zero accidental
door-unlock.

```bash
opal listen intent add unlock_door --phrase "unlock the front door"
opal listen intent add confirm_dangerous --phrase "yes I am sure" --phrase "confirm"
opal event actions create --json \
  '{"id":"ask_confirm","type":"http","method":"POST","url":"http://127.0.0.1:8001/api/v1/generate","body_mode":"template","body":{"text":"Are you sure? Say: yes I am sure."},"timeout_seconds":10}'
opal event rules create --json \
  '{"id":"unlock_step1","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"unlock_door"}],"actions":["ask_confirm"]}'
opal event actions create --json \
  '{"id":"really_unlock","type":"shell","mode":"exec","executable":"/opt/opal/unlock.sh","stdin":"event_json","timeout_seconds":10}'
opal event rules create --json \
  '{"id":"unlock_step2","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"confirm_dangerous"}],"actions":["really_unlock"]}'
```

### 29. Test rule wiring offline — no voice, no side effects

`rules test` evaluates a rule against a sample event *without executing*;
`actions execute` runs the action against a canned event on demand. Develop the
whole chain on the bus, then let the real listener drive it.

```bash
opal event rules test voice_to_look --event-json \
  '{"topic":"opal.listen.intent.match","data":{"intent_name":"describe_scene","slot_values":{}}}'
opal event actions execute describe_scene --event-json \
  '{"topic":"opal.listen.intent.match","data":{"intent_name":"describe_scene"}}'
opal event topics
```

### 30. Spoken morning briefing, built from real telemetry

A cron job composes last night's recognition stats into a sentence and hands it
to opal-talk. `service perf summary` is `--json`-friendly, so the
briefing writes itself.

```bash
total=$(opal listen --json service perf summary | jq -r '.count')
matched=$(opal listen --json service perf summary | jq -r '.matched_count')
p95=$(opal listen --json service perf summary | jq -r '.total_latency_ms.p95 // 0')
opal talk generate "Good morning. ${matched} of ${total} commands matched, p95 latency ${p95} milliseconds." \
  -o briefing.wav && aplay briefing.wav
```

### 31. Fleet backup in one cron line per service

Every OPAL service exposes the same save/validate/load snapshot contract —
back up the whole suite into one dated directory.

```bash
day=$(date +%F); mkdir -p /backup/opal/$day
opal listen config save --out /backup/opal/$day/listen.json
opal talk backup export --output /backup/opal/$day/talk.json
opal event backup export --output /backup/opal/$day/event.json
opal listen config validate --in /backup/opal/$day/listen.json
tar -czf /backup/opal-$day.tgz -C /backup/opal $day
```

### 32. One `opal` binary, four brains

Install the siblings together and the umbrella CLI mounts every module's
command tree under one root — with each `tree` command regenerating its own
map live, so discovery never goes stale.

```bash
opal listen tree
opal talk tree
opal think tree
opal listen --json system info
```

### 33. Contract-of-record auditing

`opal-api` publishes one OpenAPI file per service; each module also validates
its own stored config. Wire both into a release checklist so a drifted service
can never ship.

```bash
opal listen config validate
opal talk backup validate --payload talk.json
opal event backup validate --file event.json
spectral lint openapi/opal_listen_v0.2.0.yaml   # listen's pinned contract (opal-api: openapi/opal_listen.yaml)
```

### 34. A watchdog that watches the watchdogs

Poll every service's health endpoint; if any goes red, let opal-event's own
action raise the alarm through opal-talk — the assistant announces its own
illness.

```bash
cat > /opt/opal/health_sweep.sh <<'SH'
#!/usr/bin/env bash
fail=""
opal listen service live   >/dev/null 2>&1 || fail="$fail listen"
opal talk health          >/dev/null 2>&1 || fail="$fail talk"
opal event health         >/dev/null 2>&1 || fail="$fail event"
opal think health         >/dev/null 2>&1 || fail="$fail think"
[ -n "$fail" ] && opal talk generate "Warning:${fail} is unhealthy." -o /tmp/alarm.wav && aplay /tmp/alarm.wav
SH
chmod +x /opt/opal/health_sweep.sh
crontab -l | { cat; echo "*/5 * * * * /opt/opal/health_sweep.sh"; } | crontab -
```

### 35. Voice-controlled camera arming

"Start watching the door" becomes an event that toggles an opal-look monitor;
"stop" disables it. Two rules, one source of truth for the camera's state.

```bash
opal listen intent add cam_on  --phrase "watch the front door"
opal listen intent add cam_off --phrase "stop watching the door"
for on_off in on off; do
  opal event actions create --json \
    "{\"id\":\"cam_${on_off}\",\"type\":\"http\",\"method\":\"PATCH\",\"url\":\"http://127.0.0.1:8002/api/v1/monitors/front-door\",\"body_mode\":\"template\",\"body\":{\"enabled\":$([ $on_off = on ] && echo true || echo false)}}"
  opal event rules create --json \
    "{\"id\":\"cam_${on_off}_rule\",\"topic\":\"opal.listen.intent.match\",\"conditions\":[{\"path\":\"data.intent_name\",\"operator\":\"eq\",\"value\":\"cam_${on_off}\"}],\"actions\":[\"cam_${on_off}\"]}"
done
opal-look-cli monitors stream front-door
```

### 36. The intercom: speak here, hear it there

Because opal-talk is an HTTP service, a remote-room announcement is just an
`http` action with another host in the URL — speech in the study comes out of
the workshop speaker.

```bash
opal listen slot add message --type text
opal listen intent add announce --phrase "announce {message}"
opal event actions create --json \
  '{"id":"workshop_speak","type":"http","method":"POST","url":"http://workshop.local:8001/api/v1/generate","body_mode":"event_json","timeout_seconds":15}'
opal event rules create --json \
  '{"id":"announce","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"announce"}],"actions":["workshop_speak"]}'
```

### 37. Human-in-the-loop for the reasoning service

`opal-think` can only act via registered event actions — give it a *request*
action, and make voice approval the gate. You literally say "approved" to let
the model's plan execute.

```bash
opal event actions create --json \
  '{"id":"think_request","type":"http","method":"POST","url":"http://127.0.0.1:8082/api/v1/tasks","body_mode":"event_json","timeout_seconds":15}'
opal listen intent add approve_plan --phrase "approved" --phrase "go ahead"
opal event rules create --json \
  '{"id":"approval","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"approve_plan"}],"actions":["think_request"]}'
opal think task list --status queued
```

### 38. Change the assistant's voice by voice

A `change_voice` intent routes through the bus to `opal talk` profile
activation — say the words, hear the next voice immediately. Works because
both services keep hot-swappable profiles.

```bash
opal listen intent add next_voice --phrase "use a different voice" --phrase "change your voice"
cat > /opt/opal/next_voice.sh <<'SH'
#!/usr/bin/env bash
opal talk profiles activate radio
opal talk generate "This is my other voice." --stream -o - | aplay -r 22050 -f S16_LE -c 1
SH
chmod +x /opt/opal/next_voice.sh
opal event actions create --json \
  '{"id":"next_voice","type":"shell","mode":"bash","script_path":"/opt/opal/next_voice.sh","timeout_seconds":15}'
opal event rules create --json \
  '{"id":"voice_swap","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"next_voice"}],"actions":["next_voice"]}'
```

### 39. Dictation → meeting notes → spoken summary

`stt_mode open` turns listen into a dictation surface; collected transcripts
feed `opal think` for summarization and the digest comes back through talk.

```bash
opal listen config setting set --name stt_mode --value open
opal listen --json test listen --duration 600 --verbose > meeting.ndjson
jq -sr '[.[].transcript] | join("\n")' meeting.ndjson \
  | opal think ask "Summarize this meeting transcript in three sentences:" --no-stream \
  | opal talk generate - -o summary.wav && aplay summary.wav
opal listen config setting set --name stt_mode --value grammar
```

### 40. Whole-suite incident bundle

When something feels wrong, one block gathers every service's self-report —
versions, health, pipeline counters, bus stats — into a directory you can attach
to a bug report.

```bash
day=$(date +%s); mkdir -p incident-$day
opal listen --json system info           > incident-$day/listen-info.json
opal listen --json system doctor         > incident-$day/listen-doctor.json
opal listen --json pipeline status       > incident-$day/pipeline.json
opal listen --json perf summary          > incident-$day/perf.json
opal event stats                         > incident-$day/event-stats.txt
opal event executions list --limit 50    > incident-$day/executions.txt
opal talk info                           > incident-$day/talk-info.txt
opal think health                        > incident-$day/think-health.txt
opal-look-cli engines list               > incident-$day/look-engines.txt
tar -czf incident-$day.tgz incident-$day
```

---

*These recipes assume defaults unless noted. Every service documents its own
surface: `opal listen docs/CLI.md`, each sibling's `docs/`, and the contracts in
`opal-api/openapi/`.*
