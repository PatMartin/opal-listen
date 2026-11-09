![OPAL Listen — local-first voice recognition for OPAL](opal-listen.png)

# OPAL Listen
### Give your projects ears. Keep the thinking—and the controls—yours.

**"Computer, turn on the kitchen lights."**

A few words can be an interface. OPAL Listen turns spoken language into **structured events your software can understand**. It hears a wakeword, turns sound into text, finds the intended command, and extracts useful details—like the room, temperature, or timer length.

No giant cloud dependency is required for basic recognition. It is **local-first**, designed for Linux and Raspberry Pi 5, built around [Vosk](https://alphacephei.com/vosk/) with optional [faster-whisper](https://github.com/SYSTRAN/faster-whisper) refinement.

~~~text
   🎤 Microphone / WAV / test text
                   │
                   ▼
           Wakeword detection
         "computer" — wake up!
                   │
                   ▼
      Speech-to-text (Vosk)
         [optional refinement]
                   │
                   ▼
        Intent + slot matching
     "turn on the kitchen lights"
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
  intent_name:           slot_values:
  light_turn_on          room_name: kitchen
        │                     │
        └──────────┬──────────┘
                   ▼
         Local results + logs
                   │
         optional, disabled by
            default: publish
                   ▼
    OPAL Event → rules → actions
~~~

This is **the ears of [OPAL](https://github.com/PatMartin/opal)**. Pair it with [OPAL Event](https://github.com/PatMartin/opal-event) to turn recognized intents into automations; connect [OPAL Talk](https://github.com/PatMartin/opal-talk) to give those automations a voice. OPAL Listen recognizes the request—it does **not** switch a lamp on by itself.

### Why build voice interfaces this way?

- **Local by default.** Vosk runs speech recognition on your machine. A microphone and a model are enough to begin.
- **Wakeword-first live listening.** Commands are captured after a configured wakeword; multiple wakewords, confidence thresholds, and sound-alike distractors are supported.
- **Teach it plain-English phrases.** Map utterances to *intents*, then extract typed *slots*: `room_name=kitchen`, `temperature=21`, or `duration_minutes=5`.
- **Environment-aware.** Profiles can tune confidence, voice activity detection (VAD), vocabulary, and which commands are active in a kitchen, noisy room, or car.
- **Test before you trust.** Text tests, WAV fixtures, live microphone diagnostics, regression cases, and recognition performance measurements are built in.
- **Improve from its mistakes.** Review failed utterances, turn a useful failure into a training phrase, and optionally create a regression test at the same time.
- **API first.** FastAPI serves the REST interface, Typer is its command-line client, and a single-page browser console talks to the same API.
- **Controlled event integration.** Real live-microphone matches can publish `opal.listen.intent.match` to OPAL Event. Event publication starts **disabled**, deliberately.

> **A useful distinction:** `test text` and `test audio` are *diagnostic tools*. They test recognition and can write local logs, but **do not publish external intent-match events**. Live microphone recognition publishes only when event publication is enabled, and even then delivery is best-effort.

## Installation

### 1. Get the source and dependencies

**Recommended:** Linux or Raspberry Pi OS (64-bit) with **Python 3.11–3.13** and a microphone if you want live audio. Text-only experiments don't need a microphone.

On Debian, Ubuntu, or Raspberry Pi OS, install the PortAudio runtime:

~~~bash
sudo apt update
sudo apt install -y libportaudio2

git clone https://github.com/PatMartin/opal-listen.git
cd opal-listen

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
~~~

The install provides two entry points:

| Command | Purpose |
| --- | --- |
| `opal listen ...` | Typer CLI for recognition, catalogs, troubleshooting, and configuration |
| `opal-listen-svc` | FastAPI service and live listening engine |

You can also run the service with `python -m opal_listener`. The optional Whisper refiner needs the extra dependency:

~~~bash
pip install -e ".[dev,whisper]"
~~~

**Note:** The Python package name is `opal-listener`, the GitHub project is `opal-listen`, its CLI namespace is `opal listen`, and the installed service binary is `opal-listen-svc`.

### 2. Download a Vosk speech model

Choose an English Vosk model from the [official model catalog](https://alphacephei.com/vosk/models). The small US-English model, `vosk-model-small-en-us-0.15`, is a practical starting point for edge hardware. Download and extract it into a directory of your choice.

Point OPAL Listen at the **extracted model directory**, which should contain subdirectories such as `am/` and `conf/`:

~~~bash
export OPAL_LISTENER_VOSK_MODEL_PATH="$HOME/models/vosk-model-small-en-us-0.15"

# Confirm the model really is there:
ls "$OPAL_LISTENER_VOSK_MODEL_PATH"
~~~

The default model path in the code is machine-specific, so **set this variable** on a fresh installation.

### 3. Start the API service

~~~bash
# Local-only is safest for your first run.
export OPAL_LISTENER_HOST=127.0.0.1
opal-listen-svc
~~~

Keep it running. In another terminal, from the repository directory:

~~~bash
source .venv/bin/activate

opal listen system ping
opal listen system doctor
opal listen system devices
~~~

On first start, the service migrates its SQLite database (by default `var/opal_listener.db`) and seeds the supplied YAML catalogs. You'll have a `computer` wakeword, lights/timer/temperature/time/cancel intents, room and numeric slots, and four sample profiles.

**Open in your browser:**

| Interface | Default URL |
| --- | --- |
| API | `http://127.0.0.1:8000` |
| Swagger UI | `http://127.0.0.1:8000/docs` |
| Built-in web console | `http://127.0.0.1:8000/ui/opal-listen.html` |

The application **binds all interfaces by default** (`0.0.0.0`) and does not include API authentication. The local-only override above is intentional. Do not expose the default server to the public internet; use a trusted network and appropriate access controls if you make it reachable from other machines.

### 4. Make your first recognition

~~~bash
# No microphone required: test the NLU side immediately.
opal listen test text "computer, turn on the kitchen light"

# Replay a real included WAV file through wakeword + STT + intent matching.
opal listen test audio \
  opal_listener/tests/audio/fixtures/golden_kitchen_light_on.wav \
  --require-wakeword

# Or listen to the microphone; Ctrl-C stops this CLI session.
opal listen test listen
~~~

A successful interpretation looks roughly like:

~~~json
{
  "wakeword": "computer",
  "transcript": "turn on the kitchen light",
  "intent_name": "light_turn_on",
  "slot_values": {
    "room_name": "kitchen"
  },
  "matched": true
}
~~~

Additional fields include confidence scores, normalized text, timing, and diagnostic details. Actual values depend on your audio and model.

### Optional: Docker / Raspberry Pi deployment

The repo includes a multi-architecture Dockerfile and Compose configuration. From the repository root:

~~~bash
cd opal_listener
VOSK_MODELS_DIR="$HOME/models" docker compose up -d --build
~~~

Compose mounts models read-only, stores SQLite data in a named volume, and passes through `/dev/snd` for microphone access. Its default model directory is `/models/vosk-model-small-en-us-0.15`. On a host with a different audio group, supply `AUDIO_GID`. See [the quickstart](docs/QUICKSTART.md) for details.

## The complete Typer command tree

This is the **implemented** command tree, not a wish list. It follows the [actual CLI modules](opal_listener/cli/) and their [complete CLI reference](docs/CLI.md).

~~~text
opal
└── listen
    │  Global options (after "listen", before the subcommand):
    │  --api-url URL                 [env OPAL_LISTEN_API_URL; localhost:8000]
    │  --xpub URL                    [env OPAL_EVENT_XPUB_URL; default tcp://127.0.0.1:5555]
    │  --xsub URL                    [env OPAL_EVENT_XSUB_URL; default tcp://127.0.0.1:5556]
    │  --json                        raw JSON output
    │  --timeout SECONDS             HTTP timeout, default 60
    │
    ├── wakeword
    │   ├── list
    │   ├── get WAKEWORD
    │   ├── add NAME                 [--threshold FLOAT] [--enabled/--disabled]
    │   ├── update WAKEWORD          [--name NAME] [--threshold FLOAT]
    │   │                            [--enabled/--disabled]
    │   └── remove WAKEWORD
    │
    ├── intent
    │   ├── list
    │   ├── get INTENT
    │   ├── add NAME                 [--description TEXT] [--threshold FLOAT]
    │   │                            [--enabled/--disabled] [--phrase PHRASE]...
    │   ├── update INTENT            [--name NAME] [--description TEXT]
    │   │                            [--threshold FLOAT] [--enabled/--disabled]
    │   ├── remove INTENT
    │   ├── phrases INTENT
    │   ├── add-phrase INTENT PHRASE
    │   └── remove-phrase INTENT PHRASE_OR_ID
    │
    ├── slot
    │   ├── list
    │   ├── get SLOT
    │   ├── add NAME                 [--type list|number|text]
    │   │                            [--description TEXT] [--value VALUE]...
    │   ├── update SLOT              [--name NAME] [--type TYPE]
    │   │                            [--description TEXT]
    │   ├── remove SLOT
    │   ├── values SLOT
    │   ├── add-value SLOT VALUE     [--alias ALIAS]...
    │   ├── remove-value SLOT VALUE
    │   ├── aliases SLOT VALUE
    │   ├── add-alias SLOT VALUE ALIAS
    │   └── remove-alias SLOT VALUE ALIAS
    │
    ├── profile
    │   ├── list
    │   ├── get PROFILE
    │   ├── add NAME                 [--description TEXT] [--activate]
    │   │                            [--wakeword NAME]... [--threshold KEY=FLOAT]...
    │   │                            [--vad KEY=VALUE]... [--intent NAME]...
    │   │                            [--slot NAME]... [--model KEY=PATH]...
    │   ├── update PROFILE           [--name NAME] [--description TEXT]
    │   │                            [--wakeword NAME]... [--threshold KEY=FLOAT]...
    │   │                            [--vad KEY=VALUE]... [--intent NAME]...
    │   │                            [--slot NAME]... [--model KEY=PATH]...
    │   ├── activate PROFILE
    │   └── remove PROFILE
    │
    ├── setting
    │   ├── list
    │   └── set KEY VALUE
    │
    ├── test
    │   ├── text TEXT                [--save/--no-save]
    │   │                            [--expect-intent NAME] [--verbose]
    │   ├── audio WAV                [--save/--no-save] [--require-wakeword]
    │   │                            [--expect-intent NAME] [--verbose]
    │   ├── listen                   [--count N] [--duration SECONDS]
    │   │                            [--start/--no-start] [--verbose]
    │   ├── listen-debug             [--count N] [--duration SECONDS]
    │   │                            [--start/--no-start]
    │   └── regression               [--all]
    │
    ├── pipeline
    │   ├── start
    │   ├── stop
    │   └── status
    │
    ├── failed
    │   ├── list                     [--reviewed/--unreviewed]
    │   │                            [--promoted/--not-promoted]
    │   │                            [--limit N] [--offset N]
    │   ├── get ID
    │   ├── add TRANSCRIPT           [--expected-intent NAME] [--notes TEXT]
    │   │                            [--log-id ID]
    │   ├── update ID                [--expected-intent NAME] [--notes TEXT]
    │   │                            [--reviewed/--unreviewed]
    │   ├── remove ID
    │   └── promote ID               --intent NAME [--regression/--no-regression]
    │
    ├── regression
    │   ├── list
    │   ├── get ID
    │   ├── add NAME PHRASE          --expect-intent NAME [--slot KEY=VALUE]...
    │   │                            [--enabled/--disabled]
    │   ├── update ID                [--name NAME] [--phrase PHRASE]
    │   │                            [--expect-intent NAME] [--slot KEY=VALUE]...
    │   │                            [--enabled/--disabled]
    │   └── remove ID
    │
    ├── log
    │   ├── list                     [--limit N] [--cursor TOKEN] [--intent NAME]
    │   │                            [--matched/--no-match]
    │   ├── get ID
    │   └── count
    │
    ├── perf
    │   ├── last
    │   ├── summary                  [--limit N]
    │   └── history                  [--limit N] [--cursor TOKEN]
    │
    ├── config
    │   ├── validate                 [--from-dir DIR]
    │   ├── backup
    │   ├── backups
    │   ├── download FILENAME        [--out PATH]
    │   ├── restore FILENAME         [--yes]
    │   ├── delete-backup FILENAME   [--yes]
    │   ├── export                   [--out-dir DIR]
    │   └── import                   [--from-dir DIR] [--yes]
    │
    ├── system
    │   ├── doctor
    │   ├── devices
    │   ├── live
    │   ├── ready
    │   ├── info
    │   └── ping
    │
    └── event
        └── publish TOPIC MESSAGE     (direct ZeroMQ PUB; not REST)
~~~

Try `opal listen --help`, `opal listen intent --help`, or `opal listen profile update --help` for Typer's option details and shell completion.

**Global flags go right after `listen`:** `opal listen --json intent list` is correct; `opal listen intent list --json` is not.

## In-depth CLI guide: make the microphone earn its keep

### The vocabulary: wakewords, intents, slots, profiles

| Concept | In human terms | Example |
| --- | --- | --- |
| **Wakeword** | "Start paying attention." | `computer`, `hey opal` |
| **Transcript** | "Here's what I heard." | `turn on the kitchen light` |
| **Intent** | "Here's what the person wants." | `light_turn_on` |
| **Slot** | "Here's the detail that makes it actionable." | `room_name=kitchen` |
| **Profile** | "Here's how to listen in this environment." | `noisy_room` |
| **Regression test** | "Never forget how to understand this phrase." | `lounge_on` |
| **Failed utterance** | "I didn't understand. Want to teach me?" | `switch the kitchen lamp on` |

Most commands talk to the REST server; **`opal listen event publish` is the exception**, sending directly over ZeroMQ. That keeps the server, web UI, and CLI working with one shared configuration.

### Use case 1: Make sure "computer" really means "computer"

Begin with wakewords. The built-in `computer` wakeword is enabled after the first seed.

~~~bash
opal listen wakeword list
opal listen wakeword get computer

# Add another, but don't enable it until you're ready.
opal listen wakeword add "hey opal" --threshold 0.85 --disabled

# Test/tune a wakeword when you choose to enable it.
opal listen wakeword update "hey opal" --enabled --threshold 0.9

# See or adjust the sound-alike words that should NOT wake you.
opal listen setting list
opal listen setting set wakeword_distractors "commuter,compute,computing,commute"
~~~

**Why it matters:** The Vosk wakeword path uses a fast candidate detector and confirmation stage with distractor words. `test listen-debug` displays candidates, confirmations, rejections, and sound levels. A higher threshold can lower false triggers at the cost of missed commands.

~~~bash
opal listen test listen-debug --duration 20
~~~

The live pipeline is **wakeword gated**. `test text` is an intentional diagnostic shortcut; it isn't proof your microphone would wake on the same sentence.

### Use case 2: Talk to the kitchen (without building a smart kitchen first)

The seed catalogs already understand rooms, light switching, temperature, timers, and cancellation. Try them before writing any code:

~~~bash
opal listen intent list
opal listen intent get light_turn_on
opal listen slot get room_name

opal listen test text "computer, turn on the kitchen light" --expect-intent light_turn_on
opal listen test text "computer, switch off the lounge lights" --expect-intent light_turn_off
opal listen test text "computer, set a timer for five minutes" --expect-intent timer_set
opal listen test text "computer, what time is it" --expect-intent get_time
opal listen test text "computer, never mind" --expect-intent cancel
~~~

What you're looking for: an **intent name**, extracted **slot values**, a **matched** flag, confidence, and latency. The alias `lounge` maps to the canonical slot value `living_room`. Spoken numbers can become numeric slots.

These examples **recognize** requests. To control appliances, you still connect the intent to an executor such as OPAL Event.

### Use case 3: Teach OPAL to change light colors

Let's add a new `color` slot and a new `light_color` intent, then verify that it works.

~~~bash
# The canonical color might be "warm_white", but users may say "cozy".
opal listen slot add color \
  --description "Colors understood by the light controller" \
  --value red --value blue --value warm_white

opal listen slot add-alias color warm_white cozy
opal listen slot values color

# Reuse the built-in room_name slot.
opal listen intent add light_color \
  --description "Set the color of a room's lights" \
  --phrase "make [the] {room_name} (light | lights) {color}" \
  --phrase "set [the] {room_name} (light | lights) to {color}"

# Ask for the new command without touching a real bulb.
opal listen test text "computer, set the kitchen lights to cozy" \
  --expect-intent light_color --verbose
~~~

The intended interpretation is `light_color` with `room_name=kitchen` and `color=warm_white`.

Put a lock on that behavior with a regression test:

~~~bash
opal listen regression add cozy_kitchen \
  "set the kitchen lights to cozy" \
  --expect-intent light_color \
  --slot room_name=kitchen \
  --slot color=warm_white

opal listen test regression
opal listen system doctor
~~~

The doctor checks that the loaded speech model knows the new vocabulary. **A text test can pass even when audio recognition struggles with an unknown word.** A larger Vosk model or different phrasing may help.

#### The tiny phrase language

| Syntax | Meaning | Example |
| --- | --- | --- |
| `words` | Literal words | `what time is it` |
| `{slot_name}` | Extract a typed slot | `{room_name}` |
| `[the]` | Optional words | `turn on [the] lights` |
| `(light | lights)` | Alternatives | `(light | lights)` |

All referenced slots must already exist; invalid/ambiguous syntax is rejected. Keep templates focused—one template expands to at most 128 variants. Add new ways of saying something without creating a new intent every time:

~~~bash
opal listen intent add-phrase light_turn_on \
  "illuminate [the] {room_name} (light | lights)"

opal listen intent phrases light_turn_on
~~~

### Use case 4: Make "the lounge" and "the living room" mean the same place

Aliases turn messy human phrasing into clean machine values.

~~~bash
opal listen slot values room_name
opal listen slot aliases room_name living_room

# Add another name for the same physical room.
opal listen slot add-alias room_name living_room "great room"

opal listen test text "computer, turn on the great room light" \
  --expect-intent light_turn_on

# Remove it if it causes collisions:
opal listen slot remove-alias room_name living_room "great room"
~~~

Other slot types:

~~~bash
# Numeric quantities: "twenty one" -> 21
opal listen slot get temperature

# A fresh numeric slot:
opal listen slot add brightness_percent \
  --type number --description "Brightness percentage"

# Text slots are for open recognition, not the bounded grammar mode.
opal listen setting set stt_mode open
# Restore grammar-focused recognition after exploring:
opal listen setting set stt_mode grammar
~~~

**Tip:** List slots constrain words to known values; numeric slots parse numbers; text slots require `stt_mode=open`. Changing modes can affect accuracy and vocabulary behavior—compare audio tests and run regressions afterwards.

### Use case 5: Listen in a noisy room, then switch back

A kitchen and a car aren't acoustically the same. Profiles let you select the right thresholds and VAD timing without changing every intent.

~~~bash
opal listen profile list
opal listen profile get noisy_room
opal listen profile get car

# Monitor room noise first.
opal listen test listen-debug --duration 20

# Try the built-in TV/music profile.
opal listen profile activate noisy_room
opal listen test listen-debug --duration 30

# Go back when you leave the noisy environment.
opal listen profile activate default
~~~

You can build a new profile, too:

~~~bash
opal listen profile add workshop \
  --description "Workshop with fans running" \
  --wakeword computer \
  --threshold wakeword=0.9 \
  --threshold intent=0.8 \
  --vad energy_threshold_dbfs=-35 \
  --vad noise_margin_db=12 \
  --vad silence_end_ms=900

opal listen profile activate workshop
opal listen profile get workshop

# Return to ordinary listening, then remove the experiment.
opal listen profile activate default
opal listen profile remove workshop
~~~

A profile can also select named intents/slots using repeatable `--intent` and `--slot` flags or override the model path with `--model vosk_model=/path/to/model`.

**Watch out:** On profile **update**, provided lists/maps (`--wakeword`, `--intent`, `--slot`, `--threshold`, `--vad`, `--model`) **replace** the corresponding stored collection; they do not merge. Omitted collections remain unchanged. Check `profile get` before a complicated update.

Useful VAD settings include `energy_threshold_dbfs` (minimum speech energy), `noise_margin_db` (how far above noise), `silence_end_ms` (how long silence ends a command), and `max_utterance_ms` (maximum command length). If your sentence is being chopped off, try increasing `silence_end_ms`; if it waits forever, consider decreasing it.

### Use case 6: Debug the microphone like an engineer

When recognition fails, it's easy to blame the AI. Usually the first question is simpler: **Did the microphone hear you?**

~~~bash
opal listen system devices
opal listen setting list

# Select an input device by index or a substring of its name.
opal listen setting set audio_device "USB"

opal listen pipeline status
opal listen pipeline start
opal listen test listen-debug --no-start --duration 30
opal listen pipeline status
opal listen pipeline stop
~~~

The live debug stream includes signal level (dBFS), partial speech, wakeword candidates/confirmation/rejection, speech start/end, and final interpretations. It helps distinguish four very different problems:

| Symptom | First check |
| --- | --- |
| No changing audio level | Wrong microphone device or PortAudio permissions |
| Wakeword candidates, no confirmations | Threshold/distractors/noise |
| Correct wakeword, truncated sentence | VAD silence/endpoint settings |
| Clear transcript, wrong intent | Phrase templates, slots, and intent thresholds |

Live listening:

~~~bash
opal listen test listen --count 3
opal listen test listen --duration 60
opal listen test listen --no-start  # watch an already-running pipeline
~~~

`test listen` normally starts the pipeline if needed and stops only a pipeline that *it* started. Use `--no-start` to observe an existing pipeline without changing its lifecycle.

Want the service to start listening on every boot?

~~~bash
opal listen setting set auto_start_listener true
opal listen pipeline status
~~~

The setting takes effect on the next service startup; use `pipeline start` when you want to start immediately.

### Use case 7: Reproduce a voice command with an audio file

Recordings are invaluable when you tune STT or wakeword behavior. The repository ships WAV fixtures so you can test without re-speaking a sentence.

~~~bash
opal listen test audio resources/cmds/cmd-kitchen-lights-on.wav \
  --require-wakeword --verbose

opal listen test audio resources/cmds/cmd-kitchen-lights-off.wav \
  --expect-intent light_turn_off

opal listen test audio resources/cmds/cmd-cancel.wav \
  --expect-intent cancel
~~~

Or make your own (Linux with ALSA):

~~~bash
# Four seconds, mono, 16-bit PCM at 16 kHz.
arecord -f S16_LE -r 16000 -c 1 -d 4 ./my-command.wav

opal listen test audio ./my-command.wav \
  --require-wakeword --verbose
~~~

`--require-wakeword` is important: the audio endpoint does **not** require a wakeword unless you ask it to. Use it when you want the file test to resemble the live microphone's gate. `--no-save` avoids writing a diagnostic recognition log. `--expect-intent` makes the command fail if it matched the wrong intent—ideal for a script.

### Use case 8: Turn mistakes into improvements

Real humans say things you didn't anticipate. Instead of guessing which phrases to add, **look at what failed**.

~~~bash
opal listen log count
opal listen log list --no-match --limit 20
opal listen failed list --unreviewed
opal listen failed list --not-promoted --limit 20

# Inspect a specific saved failure from the list.
opal listen failed get 3
~~~

If failure `3` is genuinely another way to ask for lights on:

~~~bash
# Promotes its transcript to a phrase; creates a regression case by default.
opal listen failed promote 3 --intent light_turn_on

opal listen intent phrases light_turn_on
opal listen test regression
~~~

If the phrase was background TV or noise, mark it reviewed instead of training on it:

~~~bash
opal listen failed update 4 --reviewed --notes "TV talking in the background"
~~~

You can also add a failure manually:

~~~bash
opal listen failed add "switch the kitchen lamp on" \
  --expected-intent light_turn_on \
  --notes "Try this phrasing in an audio test"
~~~

When failure capture is enabled (`auto_capture_failures=true` by default), unmatched microphone or uploaded-audio commands are stored for review. A **promotion** may replace detected slot values in the new phrase with placeholders, making one correction useful in multiple rooms.

### Use case 9: Treat recognition like tested software

Your voice commands are an interface. Changes to phrases and thresholds can break it just as surely as changes to code.

~~~bash
# Positive: intent and canonical slot
opal listen regression add lounge_lights \
  "switch on the lounge lights" \
  --expect-intent light_turn_on \
  --slot room_name=living_room

# Negative: a random sentence must not be a command
opal listen regression add not_a_command \
  "order a pizza" --expect-intent no_match

# Keep the contract of speech intact
opal listen test regression
opal listen regression list
~~~

Add `--all` to include disabled tests. A failing regression command exits nonzero, so it can guard future configuration changes or continuous integration.

For faster experimentation, test a single utterance without saving a log:

~~~bash
opal listen test text "computer, turn on the kitchen light" \
  --expect-intent light_turn_on --no-save
~~~

### Use case 10: Measure latency and match rate, not just "it feels fast"

~~~bash
opal listen perf last
opal listen perf summary
opal listen perf summary --limit 500
opal listen perf history --limit 30

opal listen log count
opal listen log list --intent light_turn_on --limit 20
~~~

The performance summary includes match rate, average confidence, and stage timing (wakeword, STT, intent, total) with averages and percentiles. If the end-to-end experience is slow, identify the slow stage before adjusting settings.

Want to experiment with an optional second-pass STT model?

~~~bash
# Requires having installed pip install -e ".[dev,whisper]".
opal listen setting set stt_refiner faster_whisper
opal listen setting set whisper_model tiny.en
opal listen setting set whisper_compute_type int8

# Compare your saved audio examples and performance.
opal listen perf summary
opal listen test regression

# Go back to the default Vosk-only path:
opal listen setting set stt_refiner none
~~~

The optional refiner runs **after** the first recognition pass; it is not the default wakeword detector. Model loading and inference consume resources, so benchmark it on the actual machine.

### Use case 11: Connect voice recognition to OPAL Event

This is where OPAL Listen becomes part of the larger system.

**Recognition → intent event → rule → action:**

~~~text
"computer, turn on the kitchen light"
              │
              ▼
OPAL Listen: intent_name=light_turn_on
             slot_values.room_name=kitchen
              │
              ▼  publication enabled explicitly
topic: opal.listen.intent.match
              │
              ▼
OPAL Event: rule checks intent + room
              │
              ▼
A trusted script, local HTTP endpoint, or Python plugin
~~~

In current code, the **default delivery transport is HTTP** to OPAL Event's `POST /api/v1/events/` endpoint (default `http://127.0.0.1:8080`). Direct ZeroMQ is an alternative, chosen at **server startup**.

Start OPAL Event separately, then configure OPAL Listen **before launching its server**:

~~~bash
# Use the OPAL Event REST endpoint (default mode).
export OPAL_EVENT_TRANSPORT=http
export OPAL_EVENT_API_URL=http://127.0.0.1:8080
# If OPAL Event uses bearer auth:
# export OPAL_EVENT_API_TOKEN='your-token'

opal-listen-svc
~~~

In another shell, enable external publication:

~~~bash
opal listen setting set event_publication_enabled true
opal listen pipeline start
opal listen test listen-debug --no-start
~~~

Speak a real matching command into the microphone. **Matched, non-deduplicated live microphone recognitions** can publish `opal.listen.intent.match`. Test-text and test-audio results deliberately **do not**, even when publishing is enabled. In OPAL Event, inspect the topic or configure a rule there (the stand-alone Event CLI is `opal-event` if installed):

~~~bash
opal-event list --topic opal.listen.intent.match
opal-event rules list
~~~

One subtlety: an OPAL Listen intent event's recognition result is nested inside the outer OPAL event under `data`, so an Event rule matching the recognized intent can use **`data.intent_name`** and the room can use **`data.slot_values.room_name`**. Keep the exact published event schema in view when creating cross-service rules.

To use the ZeroMQ publisher instead, set **before launching the listener service**:

~~~bash
export OPAL_EVENT_TRANSPORT=zmq
export OPAL_EVENT_XPUB_URL=tcp://127.0.0.1:5555
opal-listen-svc
~~~

Despite its legacy name, `OPAL_EVENT_XPUB_URL` in the listener points at **OPAL Event's inbound XSUB producer endpoint**, normally port 5555. Do not set `OPAL_EVENT_TRANSPORT=both`: the listener rejects that mode because duplicate deliveries could trigger duplicate actions.

**Best-effort means best-effort:** Failure to reach OPAL Event must not block microphone recognition. HTTP publication uses a bounded worker queue; broker failure or queue overflow may drop events. Do not use this as an exactly-once or durable command queue.

#### Send a test event straight onto the bus

There is also a separate CLI command for manually publishing a ZeroMQ event, even when the microphone isn't running:

~~~bash
opal listen event publish demo.voice "hello from opal-listen"
opal listen event publish demo.intent '{"intent_name":"light_turn_on","room":"kitchen"}'

# A different broker:
opal listen --xpub tcp://127.0.0.1:5555 \
  event publish demo.voice '{"message":"test"}'
~~~

This **bypasses the REST API** and opens a short-lived ZeroMQ PUB connection. It wraps plain text or JSON into OPAL Event's two-frame event envelope. The command only confirms local send—not subscriber delivery. Topic strings must follow lower-case dot-separated conventions.

### Use case 12: Back up a working voice configuration before getting creative

Once voice commands work, protect that work.

~~~bash
# Server-side SQLite backup
opal listen config backup
opal listen config backups

# Export the six editable YAML catalogs:
opal listen config export --out-dir ./listener-config

# Check them before import:
opal listen config validate --from-dir ./listener-config

# Save a JSON export for inspection or versioned snapshots:
opal listen config export > listener-config.json
~~~

The YAML export has `wakewords.yaml`, `intents.yaml`, `slots.yaml`, `profiles.yaml`, `stt.yaml` and `settings.yaml`.

When you're ready to apply the edited YAML:

~~~bash
opal listen config import --from-dir ./listener-config
~~~

The import requires confirmation and makes a safety backup. It **replaces** the imported catalogs; missing entries may be deleted. Don't treat it as a merge operation. Restore a server-side backup with `opal listen config restore FILENAME` after reading the prompt and checking what will be replaced.

### A CLI cheat sheet for regular use

| What you want | Command |
| --- | --- |
| "Is it running?" | `opal listen system doctor` |
| "Which microphone?" | `opal listen system devices` |
| "Why didn't that wake me?" | `opal listen test listen-debug` |
| "Understand this sentence?" | `opal listen test text "..."` |
| "Re-run recorded speech?" | `opal listen test audio recording.wav --require-wakeword` |
| "What can I say?" | `opal listen intent list` |
| "What did it hear?" | `opal listen log list` |
| "What failed?" | `opal listen failed list --unreviewed` |
| "Train on a useful mistake?" | `opal listen failed promote ID --intent NAME` |
| "Did I break anything?" | `opal listen test regression` |
| "How fast?" | `opal listen perf summary` |
| "Change one setting?" | `opal listen setting set KEY VALUE` |
| "Switch environments?" | `opal listen profile activate noisy_room` |
| "Save what works?" | `opal listen config backup` |

## Architecture, API, and configuration

### One REST service, several ways to use it

~~~text
    Typer CLI ─────────────────┐
    Web console ───────────────┼──▶ FastAPI /api/v1/*
    Custom REST clients ───────┘              │
                                             ▼
                                   ListenerService
                                      │       │
                              ┌───────┘       └────────┐
                              ▼                        ▼
                        SQLModel / SQLite        Recognition pipeline
                      configs, logs, tests       capture → VAD → Vosk
                                                  → intent / slots
                                                       │
                                         local SSE ────┤
                                                       ▼
                                            optional OPAL Event
                                            HTTP or ZeroMQ
~~~

The **CLI uses REST** for catalog management, recognition tests, pipeline control, logs, performance, and backups. `test listen` consumes a server-sent event stream, so you can watch live results remotely without microphone processing on the CLI machine.

The browser console at `/ui/opal-listen.html` is bundled with the service, not a separate SPA build.

### Environment and settings

Process-level settings come from environment variables or a local `.env` file. Mutable recognition settings live in SQLite and can be changed with `opal listen setting set`. Exportable YAML catalogs seed the database on a fresh install and can be imported later.

| Setting | Default / notes |
| --- | --- |
| `OPAL_LISTENER_HOST` | `0.0.0.0`; restrict to `127.0.0.1` for local-only use |
| `OPAL_LISTENER_PORT` | `8000` |
| `OPAL_LISTENER_DATA_DIR` | `var`, containing SQLite and backups |
| `OPAL_LISTENER_VOSK_MODEL_PATH` | Set explicitly to your extracted model |
| `OPAL_LISTEN_API_URL` | CLI target, `http://localhost:8000` |
| `OPAL_EVENT_TRANSPORT` | `http` by default; alternative `zmq` |
| `OPAL_EVENT_API_URL` | OPAL Event HTTP target, default `http://127.0.0.1:8080` |
| `OPAL_EVENT_XPUB_URL` | ZeroMQ producer target, default `tcp://127.0.0.1:5555` |
| `event_publication_enabled` | **false** in the seeded settings; enable deliberately |
| `stt_mode` | `grammar` by default; `open` for freer dictation |
| `stt_refiner` | `none` by default; optional `faster_whisper` |
| `auto_start_listener` | false, so the microphone pipeline starts on demand |

The CLI's `setting set` updates a **single key**, reading the current strong ETag and sending a conditional update through REST. This guards against overwriting a concurrent settings change. Some process-level changes require restarting the service.

### Runtime components

- **Vosk wakeword + STT:** local inference, grammar-constrained recognition by default.
- **Audio capture + VAD:** PortAudio through `sounddevice`, framing/resampling, adaptive noise handling, endpoint decisions, and watchdog recovery.
- **NLU engine:** normalize text, expand phrase templates, match intents, parse numbers, and canonicalize slot aliases.
- **Profiles + catalogs:** reusable YAML-backed wakewords, intents, slots, and environment-specific thresholds.
- **SQLite:** persisted catalogs, recognition logs, failed utterances, regression cases, metrics, and backups.
- **SSE + event publication:** local live observation plus optional external OPAL Event integration.
- **Optional Whisper refinement:** a second transcription pass; not required for the local-first starter experience.

## Testing and development

A voice interface should be measurable, reproducible, and difficult to accidentally break.

~~~bash
# Full pytest suite (some model-dependent tests skip without a model)
pytest

# Fast development loop without slow tests or a physical Vosk model
pytest -m "not slow and not vosk"

# Lint
ruff check .

# Live-contract CLI smoke test (requires running server)
python scripts/cli_rest_test.py

# Focus on ZeroMQ envelope integration (sibling OPAL Event checkout needed)
pytest opal_listener/tests/test_zmq_events.py -q
~~~

The project includes audio fixtures, negative phrases, golden regression tests, CLI/API parity checks, and pipeline tests. The CI workflow also exercises Python 3.11–3.13. See [the full user guide](docs/USER_GUIDE.md) and [testing notes in the quickstart](docs/QUICKSTART.md).

## Repository map

~~~text
opal-listen/
├── opal-listen.png                   project artwork
├── README.md                         this guide
├── pyproject.toml                    package / CLI entry points
├── openapi/                          OpenAPI reference (opal_listen_v1.6.0.yaml + shared kernel)
├── docs/
│   ├── QUICKSTART.md                 setup walkthrough
│   ├── CLI.md                        exhaustive CLI reference
│   ├── USER_GUIDE.md                 detailed operating guide
│   └── design.md                     architecture and design
├── opal_listener/
│   ├── cli/                          Typer commands + REST client
│   ├── config/                       six seed YAML catalogs
│   ├── data/                         SQLite + Alembic migrations
│   ├── service/
│   │   ├── audio/                    capture / PCM / VAD
│   │   ├── providers/                Vosk + optional Whisper
│   │   ├── nlu/                      normalization / patterns / slots
│   │   ├── listener_service.py       orchestration
│   │   ├── http_events.py            HTTP publisher
│   │   └── zmq_events.py             ZeroMQ publisher
│   ├── view/api/                     FastAPI routes
│   ├── view/static/                  bundled web console
│   └── tests/                        unit, integration, regression, audio
├── resources/cmds/                  WAV examples
├── scripts/                         CLI / regression tooling
└── Dockerfile                        container packaging
~~~

### More to read

- [Quickstart](docs/QUICKSTART.md) — installation, setup, model, microphone, first commands.
- [Cookbook](docs/COOKBOOK.md) — 40 copy-paste recipes: 20 with opal-listen alone, 20 across the OPAL suite.
- [CLI reference](docs/CLI.md) — all flags, resource commands, outputs, and scripts.
- [User guide](docs/USER_GUIDE.md) — extensive guidance for running and tuning the listener.
- [Design](docs/design.md) — internal architecture and tradeoffs.
- [OpenAPI](openapi/opal_listen_v1.6.0.yaml) — REST interface description.
- [OPAL Event](https://github.com/PatMartin/opal-event) — turn recognized intents into guarded actions.

---

**OPAL Listen is meant to understand you—not make you memorize its commands.** Start with one wakeword and one useful intent. Add a few alternate phrases, measure the results, and keep teaching it the words people actually say.

The rest of OPAL can take it from there.
