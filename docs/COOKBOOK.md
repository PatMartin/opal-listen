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
- IDs shown (fu `7`, rule `say_result`, backup filenames) are examples — substitute
  what your service prints.
- Most mutations auto-fetch the resource ETag and send `If-Match` for you; if the
  server answers `412`, someone else changed it — rerun to re-fetch.
- Run `opal listen tree` any time for a live map of every command and flag below.

---

## Part 1 — twenty things you can only do with opal-listen

### 1. Prototype a voice command without touching a microphone

`test text` runs your words through the same normalizer, filler-word stripper,
intent matcher and slot extractor the live pipeline uses — no audio needed. With
`--expect-intent` it exits 1 on a miss, so it doubles as a unit test.

```bash
opal listen intents add lights_on --description "Switch a room's lights on" \
  --phrase "turn on the lights" --phrase "lights on" --phrase "switch the lights on"
opal listen test text "computer, please turn on the lights" --expect-intent lights_on
```

### 2. Extract structured slots from free speech

Phrases embed `{slot_name}` placeholders (and `[optional]` words). List slots take
canonical values; aliases map how people actually talk onto those values. The
result lands in `slot_values` of every test/log row.

```bash
opal listen slots add room --type list --value kitchen --value bedroom --value living_room
opal listen slots add-alias room living_room "the lounge"
opal listen intents add-phrase lights_on "turn on [the] {room} light"
opal listen test text "computer, turn on the lounge light" --expect-intent lights_on --verbose
```

### 3. Give the assistant more than one wake word

Wakewords are first-class catalog rows — add a second trigger name, tune its
confidence independently, and both work immediately in the live pipeline.

```bash
opal listen wakewords add jarvis --threshold 0.85
opal listen wakewords list
opal listen test text "jarvis, turn on the lights" --expect-intent lights_on
```

### 4. De-noise a trigger-happy wakeword

Two levers, both hot-applied: raise the wakeword's own threshold, and add
sound-alike *distractors* (comma-separated words whose transcript presence vetoes
the hit). Then watch the no-match side of the log stream to confirm.

```bash
opal listen wakewords update computer --threshold 0.9
opal listen settings set wakeword_distractors "commuter,compute,computing,commute"
opal listen logs list --no-match --limit 20
```

### 5. Regression-test a folder of golden WAVs

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

`listen-debug` streams every SSE event: a real-time dBFS level meter, partial
transcripts, VAD and wakeword events, then the final result. The fastest way to
answer "did it even hear me?".

```bash
opal listen test listen-debug --duration 60
```

### 7. Soak-test the microphone overnight

`test listen` tails the result stream only — with `--count`/`--duration` it
self-terminates, making it a clean soak harness. Pipe `--json` output into a file
and diff it in the morning.

```bash
opal listen --json test listen --duration 3600 --verbose > soak-$(date +%F).ndjson
opal listen logs count
```

### 8. Put a regression gate in front of catalog edits

Pin the phrases that matter as named regression tests (with expected slots),
then make `test regression` a hard gate — it exits 1 on any failure, before and
after every vocabulary change.

```bash
opal listen regression add lights_on_kitchen "turn on the kitchen light" \
  --expect-intent lights_on --slot room=kitchen
opal listen regression add lights_on_lounge "turn on the lounge light" \
  --expect-intent lights_on --slot room=living_room
opal listen test regression
```

### 9. Wire the service into CI, systemd and Kubernetes

Four probe shapes for four depths of "is it healthy": `ping` (TCP/HTTP up),
`live` (process + DB), `ready` (config compiles, provider loads), `doctor`
(every startup check, human-readable). All exit non-zero on failure.

```bash
opal listen system ping && opal listen system live && opal listen system ready
opal listen system doctor   # ✓/!/✗ per check: sqlite, model, microphone, intents, slots
```

### 10. Atomically retune several settings

`settings patch` applies key=value pairs in one validated, all-or-nothing
collection PATCH — nothing half-applies if one value is wrong. Verify what a key
actually controls with `settings get` first.

```bash
opal listen settings get cooldown_ms
opal listen settings patch cooldown_ms=800 dedup_window_ms=4000 filler_words="uh,um,er,please"
opal listen settings list
```

### 11. Snapshot the catalog before surgery — and roll back

`config backup` writes a timestamped SQLite copy server-side. `restore` first
takes its *own* safety backup, then swaps the database — so even a bad restore
is undoable.

```bash
opal listen config backup
opal listen config backups
opal listen config restore opal_listener_20260614T103000_manual.db --yes
```

### 12. Keep your whole voice model in git

`config export --out-dir` writes the six YAML files (wakewords, intents, slots,
profiles, settings, engines) as reviewable source. Validate offline, then import —
which auto-validates, takes a pre-import backup, and reports row counts.

```bash
opal listen config export --out-dir ./voice-config
git add voice-config && git commit -m "voice model snapshot"
opal listen config validate --from-dir ./voice-config
opal listen config import --from-dir ./voice-config --yes
```

### 13. One service, two acoustic environments

Profiles bundle wakeword selection, per-stage thresholds, VAD timing, enabled
intents/slots and model paths. Switching is instant and atomic — only one
profile is active at a time.

```bash
opal listen profiles add noisy-kitchen \
  --threshold wakeword=0.9 --threshold intent=0.85 --vad silence_end_ms=900
opal listen profiles add quiet-study \
  --threshold wakeword=0.7 --vad silence_end_ms=400
opal listen profiles activate noisy-kitchen
opal listen profiles list
```

### 14. A night-watch profile that hears only alarms

`--intent` narrows a profile to an explicit intent allowlist — after dark, the
house only answers to two commands. Switch back at dawn by re-activating the
day profile.

```bash
opal listen profiles add night-watch --intent alarm_stop --intent emergency_call
opal listen profiles activate night-watch
# morning:
opal listen profiles activate day
```

### 15. Turn "it didn't understand me" into training data

Users report misses; you log them, review, and `failed promote` writes the
corrected transcript into the right intent *and* creates a regression test —
so it can never silently break again.

```bash
opal listen failed add "light on in the kitchen" --expected-intent lights_on \
  --notes "field report: quiet voice at distance"
opal listen failed list --unreviewed
opal listen failed promote 7 --intent lights_on --regression
opal listen test regression
```

### 16. Mine the recognition logs for what people really say

Every recognition is a row: source, wakeword, normalized transcript, intent,
extracted slots, confidences, latency, and the no-match reason. Filter by intent
or by matched/no-match to find where the model leaks.

```bash
opal listen logs count
opal listen logs list --no-match --limit 50
opal listen logs list --intent lights_on --limit 100
opal listen logs get 424
```

### 17. Audit your wake-to-intent latency budget

`perf` rows break each recognition into wakeword / STT / intent-stage
milliseconds. `summary` gives avg/p50/p95/max so you can tell whether "it feels
slow" is the wakeword stage or the STT stage.

```bash
opal listen perf last
opal listen perf summary --limit 200
opal listen perf history --limit 25
```

### 18. Page the entire history with tamper-proof cursors

Logs and performance history are cursor-paginated (the server signs the cursor
to the filters you used — it can't be forged or replayed across queries). The
CLI prints the `next_cursor` hint whenever more rows exist.

```bash
opal listen logs list --limit 25
# …prints: more results: pass --cursor <token>
opal listen logs list --limit 25 --cursor "<token from previous run>"
opal listen perf history --limit 25 --cursor "<token>"
```

### 19. Hot-swap the recognition engine — no restart

Everything in the settings catalog applies live. Flip grammar-constrained STT to
open dictation, bolt on the faster-whisper refiner for a second pass, and roll it
back with `unset` — all while the pipeline keeps running.

```bash
opal listen settings set stt_mode open
opal listen settings set stt_refiner faster_whisper
opal listen settings set whisper_model base.en
opal listen test text "computer, what is the airspeed of an unladen swallow"
# back out:
opal listen settings unset stt_refiner && opal listen settings set stt_mode grammar
```

### 20. Harden a headless appliance

Pick the right mic by name, let the watchdog recover dead captures, bound the
log table, and prune old backups — all without editing a file or restarting.

```bash
opal listen system devices
opal listen settings patch audio_device="USB" audio_watchdog_timeout_ms=5000 \
  max_recognition_logs=5000 auto_start_listener=true
opal listen config backups --json | jq -r '.[3:][] | .filename' \
  | xargs -r -I{} opal listen config delete-backup {} --yes
```

---

## Part 2 — twenty things you can only do with the whole OPAL suite

Each module is small on purpose; the interesting behavior lives in the wiring.
opal-listen hears, opal-event routes (`topic` + `conditions` + `actions`),
opal-look sees, opal-think reasons (only through registered event actions), and
opal-talk speaks. `opal-api` holds the contract-of-record for every service.

### 21. "OK" — the closed voice loop

The flagship recipe: speech → intent → bus event → spoken reply. Listen
publishes `opal.listen.intent.match` on every match; an opal-event rule hands
the event to a shell action that turns it into words through opal-talk.

```bash
opal listen settings set event_publication_enabled true
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

### 22. Dry-fire the loop without a microphone

`opal listen event publish` writes straight onto the ZeroMQ bus — inject a
synthetic `opal.listen.intent.match` envelope and watch the rule fire, no
wakeword required. Then audit the action run.

```bash
opal listen event publish opal.listen.intent.match \
  '{"intent_name":"lights_on","slot_values":{"room":"kitchen"},"source":"cli-test"}'
opal event executions list
opal event list --topic opal.listen.intent.match --limit 5
```

### 23. "Opal, what do you see?" — spoken scene description

A voice intent asks the question; a shell action captures an annotated frame
through opal-look, reads the observation summary, and speaks it back. Vision
query latency stays out of the audio path until asked.

```bash
opal listen intents add describe_scene --phrase "what do you see" --phrase "describe the scene"
opal listen settings set event_publication_enabled true
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
opal listen intents add ask_brain --phrase "ask the brain {question}"
opal listen slots add question --type text
opal listen settings set event_publication_enabled true
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
opal listen logs list --intent lights_on --limit 5 --json
opal event executions list --limit 10
opal event get <event-id from the execution>
opal listen logs get <recognition-log-id>   # exact transcript + slot extraction
```

### 27. "Good night" — one phrase, a house-wide scene

One intent, one rule, many actions: opal-event fans a single event out to a talk
announcement and an HTTP action arming the camera monitor. Rules execute their
action list together.

```bash
opal listen intents add good_night --phrase "good night" --phrase "bedtime"
opal listen settings set event_publication_enabled true
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
opal listen intents add unlock_door --phrase "unlock the front door"
opal listen intents add confirm_dangerous --phrase "yes I am sure" --phrase "confirm"
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
to opal-talk. `logs count` and `perf summary` are `--json`-friendly, so the
briefing writes itself.

```bash
total=$(opal listen --json logs count | jq -r '.total')
matched=$(opal listen --json logs count | jq -r '.matched')
p95=$(opal listen --json perf summary --limit 500 | jq -r '.total_latency_ms.p95 // 0')
opal talk generate "Good morning. ${matched} of ${total} commands matched, p95 latency ${p95} milliseconds." \
  -o briefing.wav && aplay briefing.wav
```

### 31. Fleet backup in one cron line per service

Every OPAL service exposes the same export/validate/import backup contract —
back up the whole suite into one dated directory.

```bash
day=$(date +%F); mkdir -p /backup/opal/$day
opal listen config export --out-dir /backup/opal/$day/listen
opal talk backup export --output /backup/opal/$day/talk.json
opal event backup export --output /backup/opal/$day/event.json
opal listen config validate --from-dir /backup/opal/$day/listen
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
spectral lint ../opal-api/openapi/opal_listen_v1.5.0.yaml
```

### 34. A watchdog that watches the watchdogs

Poll every service's health endpoint; if any goes red, let opal-event's own
action raise the alarm through opal-talk — the assistant announces its own
illness.

```bash
cat > /opt/opal/health_sweep.sh <<'SH'
#!/usr/bin/env bash
fail=""
opal listen system live   >/dev/null 2>&1 || fail="$fail listen"
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
opal listen intents add cam_on  --phrase "watch the front door"
opal listen intents add cam_off --phrase "stop watching the door"
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
opal listen intents add announce --phrase "announce {message}"
opal listen slots add message --type text
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
opal listen intents add approve_plan --phrase "approved" --phrase "go ahead"
opal event rules create --json \
  '{"id":"approval","topic":"opal.listen.intent.match","conditions":[{"path":"data.intent_name","operator":"eq","value":"approve_plan"}],"actions":["think_request"]}'
opal think task list --status queued
```

### 38. Change the assistant's voice by voice

A `change_voice` intent routes through the bus to `opal talk` profile
activation — say the words, hear the next voice immediately. Works because
both services keep hot-swappable profiles.

```bash
opal listen intents add next_voice --phrase "use a different voice" --phrase "change your voice"
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
opal listen settings set stt_mode open
opal listen --json test listen --duration 600 --verbose > meeting.ndjson
jq -sr '[.[].transcript] | join("\n")' meeting.ndjson \
  | opal think ask "Summarize this meeting transcript in three sentences:" --no-stream \
  | opal talk generate - -o summary.wav && aplay summary.wav
opal listen settings set stt_mode grammar
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
