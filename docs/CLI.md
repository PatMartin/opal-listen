# `opal` CLI Reference

The `opal` command is the terminal interface to the opal listener. All recognition features live under
`opal listen`. You can use it to manage wakewords, intents, slots, profiles and settings, test recognition
with text, audio files or the microphone, review failures, run regression tests, and back up or restore
the configuration.

The CLI is a thin REST client. **Every command except `event publish` calls the opal listener REST API**
(`event publish` talks directly to the ZeroMQ event bus instead). It never opens the database directly.
The server must therefore be running, either locally or on another machine.

---

## Table of contents

1. [Installation and invocation](#1-installation-and-invocation)
2. [Connecting to the server](#2-connecting-to-the-server)
3. [Conventions](#3-conventions)
4. [Command tree](#4-command-tree)
5. [`wakewords`: wakeword management](#5-wakewords--wakeword-management)
6. [`intents`: intents and training phrases](#6-intents--intents-and-training-phrases)
7. [`slots`: slots, values and aliases](#7-slots--slots-values-and-aliases)
8. [`profiles`: recognition profiles](#8-profiles--recognition-profiles)
9. [`settings`: global settings](#9-settings--global-settings)
10. [`test`: recognition testing](#10-test--recognition-testing)
11. [`pipeline`: microphone pipeline control](#11-pipeline--microphone-pipeline-control)
12. [`failed`: failed utterance review](#12-failed--failed-utterance-review)
13. [`regression`: regression test management](#13-regression--regression-test-management)
14. [`logs`: recognition logs](#14-logs--recognition-logs)
15. [`perf`: performance metrics](#15-perf--performance-metrics)
16. [`config`: validation, backup, export and import](#16-config--validation-backup-export-and-import)
17. [`system`: health and diagnostics](#17-system--health-and-diagnostics)
18. [`event`: publish ZeroMQ events](#18-event--publish-zeromq-events)
19. [Walkthroughs](#19-walkthroughs)
20. [Scripting and automation](#20-scripting-and-automation)
21. [Troubleshooting](#21-troubleshooting)
22. [Appendix: CLI ↔ REST mapping](#22-appendix-cli--rest-mapping)

---

## 1. Installation and invocation

Installing the project installs the `opal` command:

```bash
cd /gh/opal/opal-listen
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

You can invoke the CLI in two equivalent ways:

```bash
opal listen --help                 # installed console script
python -m opal_listener.cli listen --help   # same CLI, run as a module
```

Start the server before you run any command:

```bash
opal-listener-server               # or: python -m opal_listener
```

### Getting help

`--help` works at every level of the command tree:

```bash
opal --help                        # top level (only the 'listen' group)
opal listen --help                 # global options and all command groups
opal listen wakewords --help       # commands in one group
opal listen wakewords add --help   # arguments and options of one command
```

Running a group without a command also prints its help:

```bash
opal listen intents                # same as: opal listen intents --help
```

### Shell completion

The CLI is built with Typer, which provides tab completion:

```bash
opal --install-completion bash     # or zsh / fish; restart the shell afterwards
opal --show-completion bash        # print the script instead of installing it
```

---

## 2. Connecting to the server

### Global options

Global options belong to the `listen` group. Put them **after `listen` and before the command group**:

```
opal listen [--api-url URL] [--xpub URL] [--xsub URL] [--json] [--timeout SECONDS] <group> <command> [ARGS] [OPTIONS]
```

| Option | Default | Meaning |
|---|---|---|
| `--api-url URL` | `$OPAL_LISTEN_API_URL`, otherwise `http://localhost:8000` | Base URL of the REST API |
| `--xpub URL` | `$OPAL_EVENT_XPUB_URL`, otherwise `tcp://127.0.0.1:5559` | ZeroMQ event bus publisher target (used by `event publish`; see [18. `event`](#18-event--publish-zeromq-events)) |
| `--xsub URL` | `$OPAL_EVENT_XSUB_URL`, otherwise `tcp://127.0.0.1:5560` | ZeroMQ event bus subscriber target (reserved for future `event subscribe`-style commands) |
| `--json` | off | Print raw API JSON instead of tables and messages |
| `--timeout SECONDS` | `60` | HTTP timeout for each request |

```bash
# Correct: global options come right after 'listen'
opal listen --json intents list
opal listen --api-url http://pi5.local:8000 --timeout 10 system doctor

# Wrong: --json after the group is rejected as an unknown option (exit code 2)
opal listen intents list --json
```

### Environment variables

Set `OPAL_LISTEN_API_URL` once so you don't have to pass `--api-url` on every call. Note that this
variable has no `ER` in it, unlike the server's `OPAL_LISTENER_*` variables. `OPAL_EVENT_XPUB_URL` and
`OPAL_EVENT_XSUB_URL` work the same way for `--xpub`/`--xsub`.

```bash
export OPAL_LISTEN_API_URL=http://192.168.1.50:8000
opal listen system ping            # talks to 192.168.1.50

# One-off override for a single command
OPAL_LISTEN_API_URL=http://pi5.local:8000 opal listen pipeline status

# --api-url takes precedence over the environment variable
opal listen --api-url http://localhost:8000 system info
```

### Checking the connection

```bash
opal listen system ping            # ✓ http://localhost:8000 is up
opal listen system info            # service name and version
```

If the server cannot be reached, the CLI prints a hint and exits with code `1`:

```
Error: cannot reach the opal listener API at http://localhost:8000 (...).
Start it with 'python -m opal_listener' or set OPAL_LISTEN_API_URL.
```

---

## 3. Conventions

### Referencing resources by name or id

Wherever a command takes a resource, you can pass its **name** or its numeric **id**:

```bash
opal listen intents get get_time   # by name
opal listen intents get 5          # by id
opal listen profiles get 4         # profile with id 4
opal listen profiles get kitchen   # profile named "kitchen"
```

Child items can also be referenced by their text:

| Item | Reference by |
|---|---|
| Training phrase | Phrase id or exact phrase text |
| Slot value | Value id or value text |
| Alias | Alias id or alias text |

```bash
opal listen intents remove-phrase test_intent "Test intent one"   # by exact text
opal listen intents remove-phrase test_intent 57                  # by phrase id
opal listen slots remove-alias room_name living_room lounge       # value and alias by text
```

Failed utterances, regression tests and log entries have no name. You always reference them by **id**.

### Output formats

By default the CLI prints human-friendly output:

| Command kind | Default output |
|---|---|
| `list`, `values`, `aliases`, ... | A table with the most important columns |
| `get` | A key/value table |
| `add`, `update`, `remove`, ... | A one-line confirmation, e.g. `✓ wakeword 'nabu' added (id 2)` |
| `test text` / `test audio` | `MATCH` or `NO MATCH`, then a JSON summary |

With `--json`, every command prints the raw API response as JSON. This is the output to use in scripts.
Confirmations become the created or updated object, or `{"status": "ok", "message": "..."}`.

```bash
opal listen wakewords list                 # table
opal listen --json wakewords list          # JSON array, suitable for jq
opal listen --json wakewords add nabu      # JSON of the created wakeword
```

> In table output, empty values are shown as `-`, and nested lists and maps are shown as compact JSON.

### Options

- **Repeatable options.** Options such as `--phrase`, `--value`, `--alias`, `--wakeword`, `--intent`,
  `--slot`, `--threshold`, `--vad` and `--model` can be given several times. Each occurrence adds one
  item.
- **Boolean flag pairs.** Booleans use paired flags such as `--enabled/--disabled`,
  `--save/--no-save` and `--reviewed/--unreviewed`. In `update` commands, leaving out both flags keeps
  the current value.
- **`key=value` options.** `--threshold`, `--vad`, `--model` (profiles) and `--slot` (regression tests)
  take `key=value` pairs. The value is parsed as a YAML scalar: `900` becomes an integer, `0.85` a float,
  `true`/`false` a boolean, and anything else a string.
- **`update` needs at least one change.** Running `update` without any option fails with
  `Error: nothing to update (pass at least one option)`.

### Quoting

Quote multi-word arguments and every training phrase. The phrase syntax uses characters that the shell
interprets (`[`, `]`, `(`, `)`, `|`, `{`, `}`):

```bash
opal listen wakewords add "hey opal"
opal listen intents add-phrase light_turn_on "turn on [the] {room_name} (light | lights)"
```

### Confirmation prompts

Destructive configuration commands (`config restore`, `config delete-backup`, `config import`) ask for
confirmation. Pass `--yes` / `-y` to skip the prompt in scripts. If you answer "no", the command prints
`Aborted!` and exits with code `1`.

> `remove` commands for wakewords, intents, slots, profiles, failed utterances and regression tests do
> **not** ask for confirmation. Take a backup first (`opal listen config backup`) if you're unsure.

### Errors and exit codes

API errors are printed as `Error: HTTP <status>: <detail>`. Validation errors list each failing field:

```
Error: HTTP 422: setting 'stt_mode' must be one of grammar, open
Error: HTTP 409: wakeword 'computer' already exists
Error: 'nabu' not found in /api/v1/wakewords/
```

Per the OPAL v1.2 standard, mutating commands for wakewords, intents, phrases, slots, slot values,
aliases, profiles and settings require a strong `If-Match`. The CLI fetches the resource's current
`ETag` automatically before every `update`/`remove`/`set`, so this is normally invisible; if the
resource changed concurrently you'll see `Error: HTTP 412: ...` (stale) and should retry the command
(it re-fetches a fresh `ETag` each time). A `428` means the server received no usable precondition at
all, which indicates a client bug rather than a concurrent edit. Every response also carries
`X-Opal-Transaction-Id`/`X-Opal-Request-Id` correlation headers, which are useful when cross-referencing
server logs.

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `1` | API error (4xx/5xx), server unreachable, resource not found, failed regression run, invalid configuration, `system doctor` errors, `--expect-intent` mismatch, or declined confirmation |
| `2` | Invalid CLI usage: unknown option, missing argument, or an out-of-range value such as `--threshold 1.5` |

---

## 4. Command tree

```
opal
└── listen [--api-url] [--xpub] [--xsub] [--json] [--timeout]
    ├── wakewords   list | get | add | update | remove
    ├── intents     list | get | add | update | remove | phrases | add-phrase | remove-phrase
    ├── slots       list | get | add | update | remove
    │               values | add-value | remove-value | aliases | add-alias | remove-alias
    ├── profiles    list | get | add | update | activate | remove
    ├── settings    list | set
    ├── test        text | audio | listen | listen-debug | regression
    ├── pipeline    start | stop | status
    ├── failed      list | get | add | update | remove | promote
    ├── regression  list | get | add | update | remove
    ├── logs        list | get | count
    ├── perf        last | summary | history
    ├── config      validate | backup | backups | download | restore | delete-backup | export | import
    ├── system      doctor | devices | live | ready | info | ping
    └── event       publish
```

---

## 5. `wakewords`: wakeword management

A wakeword opens a command session, as in *"**computer**, turn on the kitchen light"*. Names are
normalized to lowercase without punctuation.

| Command | Description |
|---|---|
| `list` | List all wakewords |
| `get WAKEWORD` | Show one wakeword (name or id) |
| `add NAME [--threshold F] [--enabled/--disabled]` | Add a wakeword |
| `update WAKEWORD [--name N] [--threshold F] [--enabled/--disabled]` | Change a wakeword |
| `remove WAKEWORD` | Delete a wakeword |

| Option | Default | Meaning |
|---|---|---|
| `--threshold F` | `0.8` (on `add`) | Confidence threshold, `0.0`–`1.0` |
| `--enabled/--disabled` | `--enabled` (on `add`) | Whether the wakeword is active |
| `--name N` | | Rename (on `update` only) |

### Examples

```bash
# List wakewords (columns: id, name, enabled, confidence_threshold, updated_at)
opal listen wakewords list

# Show one wakeword by name, or by id
opal listen wakewords get computer
opal listen wakewords get 1

# Add a wakeword with a custom threshold, disabled for now
opal listen wakewords add nabu --threshold 0.7 --disabled

# Multi-word wakewords must be quoted
opal listen wakewords add "hey opal" --threshold 0.85

# Raise the threshold and enable it in one call
opal listen wakewords update nabu --threshold 0.95 --enabled

# Rename a wakeword
opal listen wakewords update "hey opal" --name "okay opal"

# Temporarily disable the default wakeword
opal listen wakewords update computer --disabled

# Delete a wakeword
opal listen wakewords remove nabu
```

> A higher threshold means fewer false triggers but more missed wakewords. A profile can raise it
> further with `--threshold wakeword=F` (see [profiles](#8-profiles--recognition-profiles)).

---

## 6. `intents`: intents and training phrases

An intent is a named action, for example `light_turn_on`. Each intent has one or more **training
phrases**, which are templates the recognizer matches against the transcript.

| Command | Description |
|---|---|
| `list` | List intents with phrase counts |
| `get INTENT` | Show an intent and its phrases |
| `add NAME [--description D] [--threshold F] [--enabled/--disabled] [--phrase P]...` | Add an intent, optionally with phrases |
| `update INTENT [--name N] [--description D] [--threshold F] [--enabled/--disabled]` | Change an intent |
| `remove INTENT` | Delete an intent **and all its phrases** |
| `phrases INTENT` | List the phrases of an intent |
| `add-phrase INTENT PHRASE` | Add one phrase |
| `remove-phrase INTENT PHRASE` | Remove a phrase by id or exact text |

| Option | Default | Meaning |
|---|---|---|
| `--description D` | none | Free-text description |
| `--threshold F` | `0.7` (on `add`) | Minimum intent confidence, `0.0`–`1.0` |
| `--enabled/--disabled` | `--enabled` (on `add`) | Whether the intent can match |
| `--phrase P` | | Training phrase (repeatable, `add` only) |
| `--name N` | | Rename (on `update` only) |

Intent names are `snake_case` and unique. `no_match` is reserved.

### Phrase syntax

| Syntax | Meaning | Example |
|---|---|---|
| `word` | Literal word | `what time is it` |
| `{slot}` | Slot placeholder. The slot must exist and can appear only once per phrase. | `set [a] timer for {duration_minutes} minutes` |
| `[words]` | Optional words | `turn on [the] {room_name} light` |
| `(a \| b)` | Alternatives | `(light \| lights)` |

A template expands into at most 128 concrete variants. The server rejects invalid phrases with HTTP `422`
and saves nothing. Typical mistakes are an unknown slot, unbalanced brackets, or too many variants.

### Examples

```bash
# List intents (columns: id, name, enabled, confidence_threshold, phrase_count, description)
opal listen intents list

# Show an intent: a key/value table followed by a table of its phrases
opal listen intents get get_time

# Add an intent with two literal phrases and a description
opal listen intents add test_intent \
  --phrase "Test intent one" \
  --phrase "Test intent two" \
  --description "This is a test intent"

# Add an intent using optional words and alternatives
opal listen intents add play_music \
  --description "Start music playback" \
  --threshold 0.75 \
  --phrase "play [some] music" \
  --phrase "(start | put on) [some] music"

# Add an intent that uses an existing slot
opal listen intents add light_dim \
  --description "Dim the lights in a room" \
  --phrase "dim [the] {room_name} (light | lights)"

# Change the description only
opal listen intents update test_intent --description "Updated description"

# Raise the threshold and disable the intent
opal listen intents update cancel --threshold 0.9 --disabled

# Rename an intent
opal listen intents update play_music --name music_play
```

### Managing phrases

```bash
# List phrases (columns: id, phrase, created_at)
opal listen intents phrases test_intent

# Add a phrase. Always quote it.
opal listen intents add-phrase light_turn_on "illuminate [the] {room_name}"

# Remove a phrase by its exact text...
opal listen intents remove-phrase test_intent "Test intent one"

# ...or by its id, as shown by 'intents phrases'
opal listen intents remove-phrase test_intent 57

# Delete the whole intent, including its phrases
opal listen intents remove test_intent
```

> Use `opal listen test text "..."` right after you change phrases to check that the intent matches as
> expected.

---

## 7. `slots`: slots, values and aliases

A slot is a typed parameter that is extracted from the utterance, such as `room_name` or `temperature`.

| Slot type | Values | Result in `slot_values` |
|---|---|---|
| `list` | Fixed canonical values plus aliases | Canonical value, e.g. `"living_room"` |
| `number` | Spoken or digit numbers ("twenty one", "21") | Integer, e.g. `21` |
| `text` | Free text (needs `stt_mode=open`) | String |

Values and aliases only apply to `list` slots. An alias is another way to say a value, and it normalizes
to the canonical value. For example, *"lounge"* resolves to `living_room`. Underscores in values are
spoken as spaces.

### Slot commands

| Command | Description |
|---|---|
| `list` | List slots with their values and aliases |
| `get SLOT` | Show one slot as JSON (values and aliases included) |
| `add NAME [--type T] [--description D] [--value V]...` | Add a slot, optionally with initial values |
| `update SLOT [--name N] [--type T] [--description D]` | Change a slot |
| `remove SLOT` | Delete a slot. This fails while phrases still use it. |

| Option | Default | Meaning |
|---|---|---|
| `--type T` | `list` (on `add`) | `list`, `number` or `text` |
| `--description D` | none | Free-text description |
| `--value V` | | Initial value (repeatable, `add` only) |
| `--name N` | | Rename (on `update` only) |

### Value and alias commands

| Command | Description |
|---|---|
| `values SLOT` | List values with their aliases |
| `add-value SLOT VALUE [--alias A]...` | Add a canonical value, optionally with aliases |
| `remove-value SLOT VALUE` | Remove a value (by text or id) and its aliases |
| `aliases SLOT VALUE` | List the aliases of one value |
| `add-alias SLOT VALUE ALIAS` | Add an alias to a value |
| `remove-alias SLOT VALUE ALIAS` | Remove an alias (by text or id) |

### Examples: slots

```bash
# List slots (columns: id, name, slot_type, values, description).
# Values appear as "kitchen (cooking area), living_room (lounge, family room), ..."
opal listen slots list

# Show one slot as JSON
opal listen slots get temperature

# Add a list slot with initial values
opal listen slots add color --description "Bulb colors" \
  --value red --value blue --value warm_white

# Add a number slot (number slots have no values)
opal listen slots add brightness --type number --description "Percent"

# Add a list slot with no values yet
opal listen slots add test_slot --description "Test slot"

# Change the description
opal listen slots update test_slot --description "Updated test slot"

# Delete a slot. Remove or edit the phrases that use it first.
opal listen slots remove test_slot
```

### Examples: values and aliases

```bash
# List the values of a slot (columns: id, value, aliases)
opal listen slots values room_name

# Add a value
opal listen slots add-value test_slot 42

# Add a value together with aliases
opal listen slots add-value color green --alias lime --alias emerald

# Remove a value by text (its aliases are removed too)
opal listen slots remove-value test_slot 42

# List the aliases of one value
opal listen slots aliases room_name living_room

# Add an alias: "martin hall" now resolves to living_room
opal listen slots add-alias room_name living_room martin_hall

# Remove the alias again
opal listen slots remove-alias room_name living_room martin_hall
```

> After you add a value or alias, run `opal listen system doctor`. The `vocabulary` check warns if a
> word is unknown to the speech model and therefore can't be recognized from audio.

---

## 8. `profiles`: recognition profiles

A profile is an environment preset. It selects wakewords, sets threshold floors, tunes voice activity
detection (VAD), overrides model paths, and limits the enabled intents and slots. Exactly one profile is
active at a time. The seeded profiles are `default`, `noisy_room`, `car` and `kitchen`.

| Command | Description |
|---|---|
| `list` | List profiles |
| `get PROFILE` | Show a profile (name or id) |
| `add NAME [--description D] [--activate] [OPTIONS...]` | Add a profile |
| `update PROFILE [--name N] [--description D] [OPTIONS...]` | Change a profile |
| `activate PROFILE` | Make this profile active (all others are deactivated) |
| `remove PROFILE` | Delete a profile |

### Profile options (for `add` and `update`)

All of these options are repeatable:

| Option | Format | Meaning | Example |
|---|---|---|---|
| `--wakeword` | name | Wakewords used by the profile (none = all enabled) | `--wakeword computer` |
| `--threshold` | `wakeword\|intent\|stt=F` | Confidence floors | `--threshold intent=0.8` |
| `--vad` | `key=value` | VAD overrides (see the table below) | `--vad silence_end_ms=900` |
| `--intent` | intent name | Enabled intents (none = all) | `--intent get_time` |
| `--slot` | slot name | Enabled slots (none = all) | `--slot room_name` |
| `--model` | `vosk_model\|whisper_model=path` | Model path overrides | `--model vosk_model=/models/vosk-model-en-us-0.22-lgraph` |

Threshold floors combine with the per-item thresholds:
effective threshold $= \max(\text{item threshold}, \text{profile floor})$.

> **`update` replaces lists and maps.** `--wakeword`, `--intent`, `--slot`, `--threshold`, `--vad` and
> `--model` each replace the **whole** stored list or map. They do not merge with it. Always pass the
> complete set you want to keep. If you leave an option out, that field is unchanged.

### VAD keys (`--vad`)

| Key | Default | Range | Effect |
|---|---|---|---|
| `energy_threshold_dbfs` | `-45.0` | -90 to 0 | Minimum level that counts as speech |
| `noise_margin_db` | `10.0` | 0 to 40 | Speech must also be this far above the adaptive noise floor |
| `speech_start_ms` | `150` | 0 to 2000 | Continuous speech needed to count as speech start |
| `silence_end_ms` | `700` | 100 to 5000 | Silence that ends the utterance. Raise it if commands get cut off. |
| `no_speech_timeout_ms` | `4000` | 500 to 30000 | Give up if no speech follows the wakeword |
| `max_utterance_ms` | `8000` | 1000 to 60000 | Hard cap on one capture session |
| `pre_roll_ms` | `1000` | 0 to 3000 | Audio kept from before the trigger and replayed to STT |
| `use_stt_endpoint` | `true` | bool | Also end the utterance when the STT engine detects an endpoint |

The server rejects unknown keys and out-of-range values with HTTP `422`.

### Examples

```bash
# List profiles (columns: id, name, is_active, wakeword_names, confidence_thresholds,
# enabled_intents, description)
opal listen profiles list

# Show one profile by id or by name
opal listen profiles get 4
opal listen profiles get noisy_room

# Create a full profile and print the result as JSON
opal listen --json profiles add test_profile \
  --description "Test Profile" \
  --wakeword nabu \
  --threshold wakeword=0.85 --threshold intent=0.8 --threshold stt=0.6 \
  --vad energy_threshold_dbfs=-35 --vad noise_margin_db=12 \
  --vad silence_end_ms=900 --vad max_utterance_ms=10000 --vad use_stt_endpoint=false \
  --intent cancel --intent set_temperature --intent get_time \
  --slot temperature

# Create a profile and activate it immediately
opal listen profiles add late_night --description "Quiet evenings" --activate \
  --wakeword computer \
  --threshold wakeword=0.85 \
  --vad silence_end_ms=900 --vad energy_threshold_dbfs=-50 \
  --intent light_turn_on --intent light_turn_off --intent get_time --intent cancel \
  --slot room_name

# Change only the description (other fields are unchanged)
opal listen profiles update test_profile --description "Updated Test Profile"

# Replace the VAD map. Keys you leave out return to their defaults.
opal listen profiles update late_night --vad silence_end_ms=1000 --vad energy_threshold_dbfs=-50

# Use a larger Vosk model for the default profile
opal listen profiles update default --model vosk_model=/models/vosk-model-en-us-0.22-lgraph

# Switch profiles
opal listen profiles activate test_profile
opal listen profiles activate default

# Delete a profile
opal listen profiles remove test_profile
```

> You can't clear a list (for example, go back to "all intents") with `update`, because an option that
> is left out means "unchanged". To clear one, use the web GUI or the REST API, or edit the exported YAML
> and import it (see [config](#16-config--validation-backup-export-and-import)).

---

## 9. `settings`: global settings

Settings are typed global key/value pairs. The server validates the type, range and allowed choices on
`set`. Per the OPAL v1.2 standard, the REST API exposes settings as a single atomic resource
(`GET`/`PATCH /api/v1/settings/` exclusively, no per-key routes); `set` fetches the current strong `ETag`
and sends it back as `If-Match` automatically, so the update is applied atomically and safely even if
another client changed settings concurrently (a stale `If-Match` is rejected with `412`, a missing one
with `428`).

| Command | Description |
|---|---|
| `list` | Show all settings with their effective values |
| `set KEY VALUE` | Atomically set one setting (fetches the current ETag and sends it as `If-Match`) |

### Known keys

| Key | Type | Default | Allowed / range |
|---|---|---|---|
| `listener_provider` | str | `vosk` | installed providers |
| `vosk_model_path` | str | `$OPAL_LISTENER_VOSK_MODEL_PATH` | directory |
| `stt_mode` | str | `grammar` | `grammar`, `open` |
| `stt_refiner` | str | `none` | `none`, `faster_whisper` |
| `whisper_model` | str | `tiny.en` | model name or path |
| `whisper_compute_type` | str | `int8` | `int8`, `int8_float32`, `float32` |
| `wakeword_trigger` | str | `partial` | `partial`, `final` |
| `wakeword_distractors` | str | `commuter,compute,computing,commute` | comma-separated words |
| `audio_device` | str | `""` (system default) | device index or name substring |
| `audio_capture_rate` | int | `0` (auto) | 0 to 192000 |
| `audio_block_ms` | int | `100` | 20 to 500 |
| `auto_start_listener` | bool | `false` | |
| `cooldown_ms` | int | `1500` | 0 to 60000 |
| `dedup_window_ms` | int | `2000` | 0 to 60000 |
| `auto_capture_failures` | bool | `true` | |
| `filler_words` | str | `uh,um,er,please` | comma-separated words |
| `slot_fuzzy_min_score` | float | `0.85` | 0 to 1 |
| `token_fuzzy_min_score` | float | `0.8` | 0 to 1 |
| `audio_watchdog_timeout_ms` | int | `3000` | 500 to 60000 |
| `max_recognition_logs` | int | `10000` | 100 to 1000000 |

Booleans accept `true/false`, `1/0`, `yes/no` and `on/off`.

### Examples

```bash
# Show all settings
opal listen settings list

# Switch the Whisper refiner model, then switch back
opal listen settings set whisper_model medium.en
opal listen settings set whisper_model tiny.en

# Enable the optional second-pass refiner (requires the [whisper] extra)
opal listen settings set stt_refiner faster_whisper

# Choose the microphone by index or by name substring (see 'system devices')
opal listen settings set audio_device 2
opal listen settings set audio_device "USB"

# Start the microphone pipeline automatically when the server starts
opal listen settings set auto_start_listener true

# Shorten the cooldown between commands
opal listen settings set cooldown_ms 1000

# Add a sound-alike word that should never trigger the wakeword
opal listen settings set wakeword_distractors "commuter,compute,computing,commute,commuting"

# Invalid values are rejected
opal listen settings set stt_mode fast
# Error: HTTP 422: setting 'stt_mode' must be one of grammar, open

# Go back to the default value
opal listen settings set whisper_model tiny.en
```

---

## 10. `test`: recognition testing

| Command | Description |
|---|---|
| `text TEXT` | Run text through normalization, intent matching and slot extraction |
| `audio WAV` | Run a WAV file through the full wakeword → VAD → STT → intent pipeline |
| `listen` | Stream live microphone results |
| `listen-debug` | Like `listen`, plus audio levels, partial transcripts, and wakeword and VAD events |
| `regression` | Run the saved regression tests |

### `test text`

```
opal listen test text TEXT [--save/--no-save] [--expect-intent INTENT] [-v]
```

| Option | Default | Meaning |
|---|---|---|
| `--save/--no-save` | `--save` | Store the result in the recognition logs |
| `--expect-intent INTENT` | | Exit `1` if the matched intent differs (use `no_match` for negative tests) |
| `-v`, `--verbose` | off | Print the full result: alternatives, matched phrase, all latencies |

No microphone or speech model is needed. The wakeword in the text is optional.

```bash
# Basic text test
opal listen test text "computer, turn on the kitchen light"
```
```
MATCH
{
  "wakeword": "computer",
  "transcript": "turn on the kitchen light",
  "normalized_transcript": "turn on the kitchen light",
  "intent_name": "light_turn_on",
  "slot_values": {"room_name": "kitchen"},
  "wakeword_confidence": 1.0,
  "intent_confidence": 1.0,
  "matched": true,
  "total_latency_ms": 1
}
```

If nothing matches, the summary also includes `no_match_reason`:

```
NO MATCH
{ ..., "intent_name": "no_match", "matched": false, "no_match_reason": "no_candidate" }
```

| `no_match_reason` | Meaning |
|---|---|
| `empty_transcript` | Nothing was left after normalization |
| `no_candidate` | No phrase came close |
| `below_threshold` | The best intent scored below its effective threshold |
| `missing_slot` | A phrase matched, but a required slot value was not recognized |
| `no_wakeword` | Audio test with `--require-wakeword`, and no wakeword was heard |

More examples:

```bash
# An alias resolves to the canonical value: room_name = living_room
opal listen test text "computer, switch on the lounge lights"

# A spoken number becomes an integer: temperature = 21
opal listen test text "set the temperature to twenty one degrees"

# Full result, including runner-up alternatives and the winning phrase template
opal listen test text "what is the time" -v

# Try something without filling the logs
opal listen test text "order a pizza" --no-save

# Assertions for scripts: exit code 0 on match, 1 on mismatch
opal listen test text "start a ten minute timer" --expect-intent timer_set && echo OK
opal listen test text "order a pizza" --expect-intent no_match --no-save
```

### `test audio`

```
opal listen test audio WAV [--save/--no-save] [--require-wakeword] [--expect-intent INTENT] [-v]
```

| Option | Default | Meaning |
|---|---|---|
| `--save/--no-save` | `--save` | Store the result in the recognition logs |
| `--require-wakeword` | off | Return `no_match` / `no_wakeword` unless a wakeword is heard. Without it, the whole file is transcribed. |
| `--expect-intent INTENT` | | Exit `1` if the matched intent differs |
| `-v`, `--verbose` | off | Print the full result |

The file must be a 16-bit PCM WAV, mono or stereo, at any sample rate. It is uploaded to the server.

```bash
# Run a recorded command through the full pipeline
opal listen test audio resources/cmds/cmd-kitchen-lights-off.wav

# Require the wakeword, the same way the live microphone pipeline does
opal listen test audio resources/cmds/cmd-kitchen-lights-on.wav --require-wakeword

# Assert the expected intent (useful in scripts and CI)
opal listen test audio resources/cmds/cmd-cancel.wav --expect-intent cancel

# Record your own command (4 s, 16 kHz mono) and test it
arecord -f S16_LE -r 16000 -c 1 -d 4 my_command.wav
opal listen test audio my_command.wav -v
```

### `test listen` and `test listen-debug`

```
opal listen test listen       [-n COUNT] [--duration SECONDS] [--start/--no-start] [-v]
opal listen test listen-debug [-n COUNT] [--duration SECONDS] [--start/--no-start]
```

| Option | Default | Meaning |
|---|---|---|
| `-n`, `--count N` | `0` (unlimited) | Stop after N results |
| `--duration S` | `0` (unlimited) | Stop after S seconds |
| `--start/--no-start` | `--start` | Start the pipeline if it isn't running |
| `-v`, `--verbose` | off | Print full results (`listen` only. `listen-debug` always prints full results.) |

If `test listen` started the pipeline, it stops it again on exit or Ctrl-C. A pipeline that was already
running keeps running.

```bash
# Listen until Ctrl-C, then print each recognized command
opal listen test listen

# Stop after 3 results, or after 30 seconds
opal listen test listen -n 3
opal listen test listen --duration 30

# Watch a pipeline that is already running (e.g. with auto_start_listener=true),
# without starting or stopping it
opal listen test listen --no-start

# Debug view: 10 results or 10 seconds, whichever comes first
opal listen test listen-debug --count 10 --duration 10 --start
```

Example `listen-debug` output:

```
listening... say the wakeword (Ctrl-C to stop)
status: listening
     idle ███████·······················  -62.3 dBFS
wakeword_candidate {'session_id': 'a1b2c3d4e5f6', 'wakeword': 'computer'}
capturing ██████████████████············  -28.1 dBFS
… turn on the
wakeword_confirmed {'session_id': 'a1b2c3d4e5f6', 'wakeword': 'computer', 'confidence': 0.97}
speech_start {'session_id': 'a1b2c3d4e5f6'}
… turn on the kitchen light
speech_end {'session_id': 'a1b2c3d4e5f6', 'reason': 'speech_end'}
{ ...full recognition result... }
```

How to read it:

| Line | Meaning |
|---|---|
| `idle` / `capturing` bar | Live input level in dBFS. Use it to set `energy_threshold_dbfs`. |
| `… text` | Partial transcript while you speak |
| `wakeword_candidate` / `wakeword_confirmed` / `wakeword_rejected` | Wakeword detection stages |
| `speech_start` / `speech_end` | VAD boundaries. `reason` is `speech_end`, `stt_endpoint`, `no_speech` or `max_length`. |
| `status: ...` | Pipeline state changes and errors |

With `--json`, each debug event is printed as `{"event": "...", "data": {...}}`. This is handy for piping
into `jq`.

### `test regression`

```
opal listen test regression [--all]
```

Runs the saved regression tests against the current intent model and prints a table (`test_id`, `name`,
`phrase`, `expected_intent`, `actual_intent`, `passed`, `failure_reason`). The title shows the totals,
e.g. `Regression: 12/12 passed`. The command exits with `1` if any test fails.

```bash
# Run the enabled tests
opal listen test regression

# Include disabled tests
opal listen test regression --all

# Use in a script
opal listen test regression || echo "regressions detected"
```

Manage the tests themselves with [`opal listen regression`](#13-regression--regression-test-management).

---

## 11. `pipeline`: microphone pipeline control

| Command | Description |
|---|---|
| `start` | Start the live microphone pipeline (`started` or `already_running`) |
| `stop` | Stop it (`stopped` or `not_running`) |
| `status` | Show the state and counters |

```bash
opal listen pipeline status        # state: stopped
opal listen pipeline start         # ✓ pipeline started
opal listen pipeline status        # state: listening, device, sample rate, counters
opal listen pipeline stop          # ✓ pipeline stopped
```

Important `status` fields:

| Field | Meaning |
|---|---|
| `state` | `stopped`, `starting`, `listening`, `capturing` or `recovering` |
| `audio_device`, `capture_sample_rate` | The device in use and its native rate (audio is resampled to 16 kHz) |
| `sessions` vs `wakeword_detections` | Candidates opened vs wakewords confirmed. A large gap means many rejected false triggers. |
| `results` | Recognition results produced |
| `audio_restarts`, `last_error` | Watchdog and recovery activity |
| `last_audio_level` | Most recent input level in dBFS |

> Use `opal listen settings set auto_start_listener true` to start the pipeline every time the server
> starts.

---

## 12. `failed`: failed utterance review

When `auto_capture_failures` is `true` (the default), every `no_match` from the microphone or an audio
file is stored as a **failed utterance**. You review these, annotate them, and *promote* useful ones to
training phrases.

| Command | Description |
|---|---|
| `list [--reviewed/--unreviewed] [--promoted/--not-promoted] [--limit N] [--offset N]` | List failures, newest first |
| `get ID` | Show one failure |
| `add TRANSCRIPT [--expected-intent I] [--notes N] [--log-id ID]` | Record a failure manually |
| `update ID [--expected-intent I] [--notes N] [--reviewed/--unreviewed]` | Annotate a failure |
| `remove ID` | Delete a failure |
| `promote ID --intent I [--regression/--no-regression]` | Add the transcript as a training phrase of intent `I` |

| Option | Default | Meaning |
|---|---|---|
| `--limit N` | `50` | Page size (max 500) |
| `--offset N` | `0` | Skip N rows |
| `--log-id ID` | | Link to a recognition log entry |
| `--regression/--no-regression` | `--regression` | When promoting, also create a regression test |

When a failure is promoted, recognized slot values in the transcript are replaced with `{placeholders}`.
For example, *"switch the kitchen lamp on"* becomes `switch the {room_name} lamp on`.

### Examples

```bash
# Unreviewed failures only
opal listen failed list --unreviewed

# Failures that have not been promoted yet, in pages of 20
opal listen failed list --not-promoted --limit 20
opal listen failed list --not-promoted --limit 20 --offset 20

# Inspect one failure
opal listen failed get 3

# Promote it to a phrase of light_turn_on and add a regression test
opal listen failed promote 3 --intent light_turn_on

# Promote without creating a regression test
opal listen failed promote 4 --intent get_time --no-regression

# Mark a failure as reviewed and leave a note
opal listen failed update 5 --reviewed --notes "TV in background"

# Record a failure you noticed yourself, linked to log entry 42
opal listen failed add "dim the lounge" --expected-intent light_dim \
  --notes "intent does not exist yet" --log-id 42

# Delete a failure
opal listen failed remove 5
```

---

## 13. `regression`: regression test management

A regression test is a phrase that must keep resolving to an expected intent (and optionally expected
slot values). Run the tests with [`opal listen test regression`](#test-regression).

| Command | Description |
|---|---|
| `list` | List tests (columns: id, name, phrase, expected_intent, expected_slots, enabled, last_result) |
| `get ID` | Show one test |
| `add NAME PHRASE --expect-intent I [--slot k=v]... [--enabled/--disabled]` | Add a test |
| `update ID [--name N] [--phrase P] [--expect-intent I] [--slot k=v]... [--enabled/--disabled]` | Change a test. `--slot` replaces **all** expected slots. |
| `remove ID` | Delete a test |

Use `--expect-intent no_match` for negative tests: phrases that must **not** trigger any intent.
`--slot` values are parsed as YAML scalars, so `duration_minutes=10` expects the integer `10`.

### Examples

```bash
# Positive test with an expected slot value (via an alias)
opal listen regression add lounge_on "switch on the lounge lights" \
  --expect-intent light_turn_on --slot room_name=living_room

# Number slot
opal listen regression add timer_ten "set a timer for ten minutes" \
  --expect-intent timer_set --slot duration_minutes=10

# Negative test
opal listen regression add not_a_command "order a pizza" --expect-intent no_match

# Add a test that is disabled for now
opal listen regression add garage_on "lights on in the garage" --expect-intent light_turn_on --disabled

# List and inspect
opal listen regression list
opal listen regression get 3

# Disable a flaky test, change its phrase, or replace its expected slots
opal listen regression update 3 --disabled
opal listen regression update 3 --phrase "turn the lounge lights on"
opal listen regression update 3 --slot room_name=living_room

# Delete a test
opal listen regression remove 3

# Run them all
opal listen test regression
```

---

## 14. `logs`: recognition logs

Every recognition (microphone, audio file, or text with `--save`) is stored as a log entry. The number
of rows kept is limited by `max_recognition_logs`. Listing uses OPAL v1.2 cursor pagination: each page
reports `has_more`; pass `--cursor` with the opaque token from the previous page to continue. Cursors
are scoped to the exact filter set (`--intent`/`--matched`) and are rejected with `422` if reused with
different filters.

| Command | Description |
|---|---|
| `list [--limit N] [--cursor TOKEN] [--intent I] [--matched/--no-match]` | Recent logs, newest first |
| `get ID` | Show one log entry with all fields |
| `count` | Totals: `total`, `matched`, `no_match` |

| Option | Default | Meaning |
|---|---|---|
| `--limit N` | `100` | Page size (max 500) |
| `--cursor TOKEN` | | Continue from the previous page's `next_cursor` |
| `--intent I` | | Only entries for this intent |
| `--matched/--no-match` | both | Only matched, or only unmatched entries |

```bash
# The 100 newest entries
opal listen logs list

# The last 50 failures
opal listen logs list --no-match --limit 50

# Next page (the CLI prints the --cursor to pass when has_more is true)
opal listen logs list --intent light_turn_on --cursor <token-from-previous-page>

# One entry in full detail
opal listen logs get 42

# Totals
opal listen logs count
```

---

## 15. `perf`: performance metrics

| Command | Description |
|---|---|
| `last` | Latency and confidence of the most recent recognition |
| `summary [--limit N]` | Averages and percentiles over the last N recognitions (default 100, max 1000) |
| `history [--limit N] [--cursor TOKEN]` | Per-recognition rows, newest first (cursor-paginated, default 100, max 500) |

```bash
# The last recognition (prints "no recognitions yet" if there are none)
opal listen perf last

# Match rate, average confidences, and a latency table (avg/p50/p95/max)
# for the wakeword, STT, intent and total stages
opal listen perf summary
opal listen perf summary --limit 1000

# Per-recognition rows (the CLI prints the --cursor to pass when has_more is true)
opal listen perf history --limit 50
opal listen perf history --limit 50 --cursor <token-from-previous-page>
```

Example `summary` output (abbreviated):

```
          Performance (last 100)
 count                    100
 matched_count            94
 match_rate               0.94
 avg_wakeword_confidence  0.95
 avg_intent_confidence    0.93

            Latency (ms)
 metric              avg    p50   p95   max
 wakeword_latency_ms 14.2   11    31    60
 stt_latency_ms      19.8   16    44    97
 intent_latency_ms   1.1    1     2     5
 total_latency_ms    38.5   33    81    160
```

---

## 16. `config`: validation, backup, export and import

| Command | Description |
|---|---|
| `validate [--from-dir DIR]` | Validate the server configuration, or local YAML files. Exits `1` if invalid. |
| `backup` | Create a SQLite backup on the server |
| `backups` | List the server's backups (filename, size_bytes, created_at) |
| `download FILE [-o PATH]` | Download a backup to the local machine |
| `restore FILE [-y]` | Replace the database with a backup |
| `delete-backup FILE [-y]` | Delete a backup on the server |
| `export [--out-dir DIR]` | Export the configuration as JSON (stdout), or as YAML files |
| `import [--from-dir DIR] [-y]` | Replace the configuration from YAML files |

Backup filenames look like `opal_listener_20260930T093000123456_manual.db`. The suffix shows why the
backup was made, e.g. `manual` or `pre_import`.

### Validation

```bash
# Validate the server's configuration (config dir and database)
opal listen config validate

# Validate local YAML files before importing them
opal listen config validate --from-dir ./my-config
```

Output lists errors in red and warnings in yellow, then `configuration is valid` or
`configuration is invalid` (exit code `1`).

### Backups

```bash
# Create a backup on the server
opal listen config backup              # ✓ backup created: opal_listener_..._manual.db

# List backups, newest first
opal listen config backups

# Download a backup into a directory, or to a specific file
opal listen config download opal_listener_20260930T093000123456_manual.db -o ~/backups/
opal listen config download opal_listener_20260930T093000123456_manual.db -o ./before-upgrade.db

# Restore a backup. The current state is saved first, and the restore is rolled back on failure.
opal listen config restore opal_listener_20260930T093000123456_manual.db
# Replace the current database with opal_listener_...db? [y/N]: y

# Same, without the prompt (for scripts)
opal listen config restore opal_listener_20260930T093000123456_manual.db --yes

# Delete an old backup
opal listen config delete-backup opal_listener_20260929T181500000000_pre_import.db -y
```

### Export and import

The configuration is made of six YAML files: `wakewords.yaml`, `intents.yaml`, `slots.yaml`,
`profiles.yaml`, `stt.yaml` and `settings.yaml`.

```bash
# Export as one JSON bundle to stdout
opal listen config export > bundle.json

# Export as six YAML files, ready for editing or version control
opal listen config export --out-dir ./my-config

# Edit, validate, then import. The server makes a pre-import backup automatically.
$EDITOR ./my-config/intents.yaml
opal listen config validate --from-dir ./my-config
opal listen config import --from-dir ./my-config --yes

# Re-import the server's own config directory (OPAL_LISTENER_CONFIG_DIR)
opal listen config import
```

After an import, the CLI prints the backup filename, a count of imported items, and any warnings.

> `--from-dir` reads YAML files on the **client** machine and uploads them. Without it, `import` uses
> the YAML directory on the **server**.

> `import` **replaces** wakewords, intents, slots, profiles and settings. Items that are not in the YAML
> files are deleted.

---

## 17. `system`: health and diagnostics

| Command | Description |
|---|---|
| `ping` | Legacy liveness probe (`GET /healthz`) |
| `info` | Service name and version (`GET /`) |
| `doctor` | Run all health checks. Exits `1` if any check is an error. |
| `devices` | List audio input devices visible to the **server** |
| `live` | OPAL v1.2 liveness probe: process + database reachability (`GET /api/v1/health/live`) |
| `ready` | OPAL v1.2 readiness probe: configuration compiles, provider loads, etc. (`GET /api/v1/health/ready`) |

```bash
# Is the server up?
opal listen system ping

# Which version is running?
opal listen system info

# Full health check
opal listen system doctor

# Standard OPAL liveness/readiness probes (status: ok | degraded | unavailable; 503 when unavailable)
opal listen system live
opal listen system ready

# Find the microphone index or name to use for the audio_device setting
opal listen system devices
opal listen settings set audio_device 2
```

Example `doctor` output:

```
✓ sqlite           var/opal_listener.db
✓ migrations       revision 0001 (head 0001)
✓ slots            slots compile
✓ intents          6 intents, 52 phrase variants
✓ provider         vosk loaded
✓ vocabulary       all wakeword/phrase words are in the model vocabulary
! microphone       no audio input devices found
✓ listener         stopped
✓ disk             48213 MiB free
overall: ! warning
```

`✓` = ok, `!` = warning, `✗` = error. Microphone problems are only warnings, because text and audio-file
tests work without a microphone. A missing model is an error.

---

## 18. `event`: publish ZeroMQ events

`opal listen event publish` fires a message directly onto the opal event bus, independently of the REST
API. It opens a ZeroMQ `PUB` socket, connects to `--xpub`, and sends a two-frame `[TOPIC, MESSAGE]`
message; anything subscribed to that topic through the event bus receives it. `--xpub`/`--xsub` name the
two sides of that bus (an XPUB/XSUB broker); `publish` only uses `--xpub` today — `--xsub` is reserved for
future subscriber-side tooling.

| Command | Description |
|---|---|
| `publish TOPIC MESSAGE` | Publish `MESSAGE` under `TOPIC` |

| Option | Default | Meaning |
|---|---|---|
| `--xpub URL` | `$OPAL_EVENT_XPUB_URL`, otherwise `tcp://127.0.0.1:5559` | ZeroMQ event bus target that `publish` connects to |
| `--xsub URL` | `$OPAL_EVENT_XSUB_URL`, otherwise `tcp://127.0.0.1:5560` | The event bus's other endpoint; not used by `publish`, accepted here for symmetry with future event-bus tooling |

Like `--api-url`, both are **global options** that belong right after `listen`:

```bash
opal listen --xpub tcp://pi5.local:5559 event publish wakeword "computer"
```

```bash
opal listen event publish wakeword "computer detected"
opal listen event publish recognition.result '{"intent_name": "light_turn_on", "matched": true}'

# Point at a remote broker for the rest of the shell session
export OPAL_EVENT_XPUB_URL=tcp://pi5.local:5559
opal listen event publish wakeword "computer"
```

> `MESSAGE` is sent as opaque bytes (UTF-8 encoded). If it's JSON, quote it so the shell passes it through
> as one argument, as in the `recognition.result` example above.

Because `publish` opens a brand-new socket for a single message, it waits briefly after connecting before
sending — the classic ZeroMQ "slow joiner" mitigation for a short-lived publisher. There is still no
delivery confirmation (ZeroMQ `PUB` sockets are fire-and-forget), so `publish` reports success once the
message has been handed to the socket, not once a subscriber has received it.

---

## 19. Walkthroughs

### 18.1 Full CRUD tour (based on `scripts/test-cli.sh`)

This tour exercises every catalog group and cleans up after itself. You can run it on a fresh server.

```bash
# --- sanity -------------------------------------------------------------------------------
opal listen system ping
opal listen system doctor

# --- wakewords ----------------------------------------------------------------------------
opal listen wakewords list
opal listen wakewords get computer
opal listen wakewords add nabu --threshold 0.7 --disabled     # created disabled
opal listen wakewords update nabu --threshold 0.95 --enabled   # now enabled, stricter
opal listen wakewords get nabu

# --- intents and phrases ------------------------------------------------------------------
opal listen intents add test_intent \
  --phrase "Test intent one" --phrase "Test intent two" \
  --description "This is a test intent"
opal listen intents update test_intent --description "Updated description"
opal listen intents phrases test_intent
opal listen intents remove-phrase test_intent "Test intent one"
opal listen intents phrases test_intent                        # one phrase left

# --- slots, values and aliases ------------------------------------------------------------
opal listen slots add test_slot --description "Test slot"
opal listen slots update test_slot --description "Updated test slot"
opal listen slots add-value test_slot 42
opal listen slots values test_slot
opal listen slots remove-value test_slot 42
opal listen slots aliases room_name living_room
opal listen slots add-alias room_name living_room martin_hall
opal listen slots aliases room_name living_room                # now includes martin_hall
opal listen slots remove-alias room_name living_room martin_hall

# --- profiles -----------------------------------------------------------------------------
opal listen --json profiles add test_profile \
  --description "Test Profile" \
  --wakeword nabu \
  --threshold wakeword=0.85 --threshold intent=0.8 --threshold stt=0.6 \
  --vad energy_threshold_dbfs=-35 --vad noise_margin_db=12 \
  --vad silence_end_ms=900 --vad max_utterance_ms=10000 --vad use_stt_endpoint=false \
  --intent cancel --intent set_temperature --intent get_time \
  --slot temperature
opal listen profiles update test_profile --description "Updated Test Profile"
opal listen profiles activate test_profile
opal listen profiles activate default                          # switch back before removing

# --- settings -----------------------------------------------------------------------------
opal listen settings list
opal listen settings set whisper_model medium.en
opal listen settings set whisper_model tiny.en

# --- recognition --------------------------------------------------------------------------
opal listen test text "computer, turn on the kitchen light"
opal listen test audio resources/cmds/cmd-kitchen-lights-off.wav
opal listen test regression

# --- pipeline -----------------------------------------------------------------------------
opal listen pipeline status
opal listen pipeline start
opal listen test listen-debug --count 10 --duration 10 --no-start
opal listen pipeline stop

# --- clean up -----------------------------------------------------------------------------
opal listen intents remove test_intent
opal listen slots remove test_slot
opal listen profiles remove test_profile
opal listen wakewords remove nabu
```

### 18.2 Add a new intent with a new slot

```bash
# 1. Create the slot and its values
opal listen slots add color --description "Bulb colors" --value red --value blue --value warm_white
opal listen slots add-alias color warm_white cozy

# 2. Create the intent with phrases that use the slot
opal listen intents add light_color \
  --description "Change bulb color" \
  --phrase "make [the] {room_name} (light | lights) {color}" \
  --phrase "set [the] {room_name} (light | lights) to {color}"

# 3. Try it
opal listen test text "computer, set the kitchen lights to cozy"
# -> light_color, {"room_name": "kitchen", "color": "warm_white"}

# 4. Guard it with a regression test
opal listen regression add kitchen_cozy "set the kitchen lights to cozy" \
  --expect-intent light_color --slot room_name=kitchen --slot color=warm_white
opal listen test regression

# 5. Check that the speech model knows the new words
opal listen system doctor
```

### 18.3 The review and tuning loop

```bash
# 1. Use the system for a while, or listen live
opal listen test listen --duration 300

# 2. Look at what failed
opal listen logs count
opal listen failed list --unreviewed

# 3. Promote real commands that should have matched
opal listen failed promote 12 --intent light_turn_on

# 4. Mark noise as reviewed so it doesn't show up again
opal listen failed update 13 --reviewed --notes "background TV"

# 5. Make sure nothing else broke
opal listen test regression

# 6. Check latency and match rate
opal listen perf summary
```

### 18.4 Tune for a noisy room

```bash
# 1. Watch the levels while the room is noisy (but nobody is speaking)
opal listen test listen-debug --duration 20

# 2. Activate the noisy_room profile, or tune it
opal listen profiles update noisy_room \
  --threshold wakeword=0.9 --threshold intent=0.8 \
  --vad energy_threshold_dbfs=-38 --vad noise_margin_db=14 --vad silence_end_ms=600
opal listen profiles activate noisy_room

# 3. Test again
opal listen test listen-debug -n 5
```

### 18.5 Back up and move configuration to another machine

```bash
# On the source machine
opal listen config export --out-dir ./opal-config
git -C ./opal-config init && git -C ./opal-config add . && git -C ./opal-config commit -m "opal config"

# Against the target machine
export OPAL_LISTEN_API_URL=http://new-pi.local:8000
opal listen config validate --from-dir ./opal-config
opal listen config import --from-dir ./opal-config --yes
opal listen test regression
```

---

## 20. Scripting and automation

### JSON output with `jq`

```bash
# Names of all intents
opal listen --json intents list | jq -r '.[].name'

# Enabled wakewords and their thresholds
opal listen --json wakewords list | jq -r '.[] | select(.enabled) | "\(.name)\t\(.confidence_threshold)"'

# Only the intent and slots of a text test
opal listen --json test text "set the temperature to twenty one degrees" --no-save \
  | jq '{intent_name, slot_values}'

# The currently active profile
opal listen --json profiles list | jq -r '.[] | select(.is_active) | .name'

# Number of unreviewed failures
opal listen --json failed list --unreviewed --limit 500 | jq length

# Stream live results as JSON (one object per result; jq -c makes it one line each)
opal listen --json test listen | jq -c '{intent_name, slot_values}'
```

### Shell assertions

```bash
#!/usr/bin/env bash
set -euo pipefail

opal listen system ping
opal listen test text "computer, turn on the kitchen light" --no-save --expect-intent light_turn_on
opal listen test text "order a pizza"                       --no-save --expect-intent no_match
opal listen test audio resources/cmds/cmd-cancel.wav        --no-save --expect-intent cancel
echo "all checks passed"
```

### CI: guard your configuration

```bash
# Fails the job (exit 1) if the YAML is invalid or any regression test fails
opal listen config validate --from-dir ./opal-config
opal listen config import --from-dir ./opal-config --yes
opal listen test regression
```

### Bulk operations

```bash
# Add many values to a slot
for room in attic basement porch; do
  opal listen slots add-value room_name "$room"
done

# Add a phrase file line by line
while IFS= read -r phrase; do
  opal listen intents add-phrase light_turn_on "$phrase"
done < phrases.txt

# Disable all regression tests whose name starts with "wip_"
opal listen --json regression list \
  | jq -r '.[] | select(.name | startswith("wip_")) | .id' \
  | xargs -r -n1 opal listen regression update --disabled
```

> `xargs -n1` appends the id after `--disabled`. That works because options may come before positional
> arguments.

---

## 21. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Error: cannot reach the opal listener API at ...` | The server isn't running, or the URL is wrong. Start `opal-listener-server`, or set `OPAL_LISTEN_API_URL` / `--api-url`. |
| `No such option: --json` | Global options must come right after `listen`: `opal listen --json intents list`. |
| `Error: 'xyz' not found in /api/v1/...` | No resource with that name or id exists. Check with `list`. Names are normalized, so try lowercase. |
| `Error: nothing to update (pass at least one option)` | `update` was called without any change option. |
| `Error: HTTP 422: ...` | The server rejected the input (bad phrase syntax, unknown slot, value out of range). The message names the field. |
| `Error: HTTP 409: ...` | A resource with that name already exists. |
| `slots remove` fails | Phrases still use the slot. Remove or edit those phrases first. |
| Profile `update` lost a list entry | List and map options replace the stored value. Pass the full set. |
| `test audio` returns `no_wakeword` | You used `--require-wakeword` and the recording has no audible wakeword. Check with `-v`. |
| `test listen` shows nothing | Check `system devices`, `settings list` (look at `audio_device`) and `pipeline status`. Use `test listen-debug` to see levels. |
| Commands get cut off | Raise `silence_end_ms` in the active profile, or set `use_stt_endpoint=false`. |
| False wakeword triggers | Raise the wakeword threshold, add sound-alikes to `wakeword_distractors`, or set `wakeword_trigger final`. |
| `vocabulary` warning in `doctor` | A phrase or alias uses a word the speech model doesn't know. Rephrase it or use a larger model. |
| Long operations time out | Increase `--timeout`, e.g. `opal listen --timeout 300 config import -y`. |

---

## 22. Appendix: CLI ↔ REST mapping

All paths are relative to the API base URL. Commands that take a name or id first resolve it with a
`GET` on the collection. Per the OPAL v1.2 standard, every `PATCH`/`DELETE` below on wakewords, intents,
phrases, slots, slot values, aliases, profiles and settings requires a strong `If-Match`; the CLI fetches
the current `ETag` automatically before sending the mutation (missing precondition → `428`, stale → `412`).

> `event publish` has no REST counterpart: it talks directly to the ZeroMQ event bus (`--xpub`), not the
> REST API, and so is intentionally absent from the table below.

| CLI (`opal listen ...`) | REST |
|---|---|
| `wakewords list` | `GET /api/v1/wakewords/` |
| `wakewords add` | `POST /api/v1/wakewords/` |
| `wakewords get` | `GET /api/v1/wakewords/{id}` |
| `wakewords update` | `PATCH /api/v1/wakewords/{id}` (If-Match) |
| `wakewords remove` | `DELETE /api/v1/wakewords/{id}` (If-Match) |
| `intents list` | `GET /api/v1/intents/` |
| `intents add` | `POST /api/v1/intents/` |
| `intents get` | `GET /api/v1/intents/{id}` |
| `intents update` | `PATCH /api/v1/intents/{id}` (If-Match) |
| `intents remove` | `DELETE /api/v1/intents/{id}` (If-Match) |
| `intents phrases` | `GET /api/v1/intents/{id}/phrases` |
| `intents add-phrase` | `POST /api/v1/intents/{id}/phrases` |
| `intents remove-phrase` | `DELETE /api/v1/intents/{id}/phrases/{phrase_id}` (If-Match) |
| `slots list` | `GET /api/v1/slots/` |
| `slots add` | `POST /api/v1/slots/` (+ `POST .../values` per `--value`) |
| `slots get` | `GET /api/v1/slots/{id}` |
| `slots update` | `PATCH /api/v1/slots/{id}` (If-Match) |
| `slots remove` | `DELETE /api/v1/slots/{id}` (If-Match) |
| `slots values` | `GET /api/v1/slots/{id}/values` |
| `slots add-value` | `POST /api/v1/slots/{id}/values` (+ `POST .../aliases` per `--alias`) |
| `slots remove-value` | `DELETE /api/v1/slots/{id}/values/{value_id}` (If-Match) |
| `slots aliases` | `GET /api/v1/slots/{id}/values/{value_id}/aliases` |
| `slots add-alias` | `POST /api/v1/slots/{id}/values/{value_id}/aliases` |
| `slots remove-alias` | `DELETE /api/v1/slots/{id}/values/{value_id}/aliases/{alias_id}` (If-Match) |
| `profiles list` | `GET /api/v1/profiles/` |
| `profiles add` | `POST /api/v1/profiles/` |
| `profiles get` | `GET /api/v1/profiles/{id}` |
| `profiles update` | `PATCH /api/v1/profiles/{id}` (If-Match) |
| `profiles remove` | `DELETE /api/v1/profiles/{id}` (If-Match) |
| `profiles activate` | `POST /api/v1/profiles/{id}/activate` |
| `settings list` | `GET /api/v1/settings/` |
| `settings set` | `PATCH /api/v1/settings/` (If-Match; atomic, may set several keys) |
| `test text` | `POST /api/v1/recognition/test_text` |
| `test audio` | `POST /api/v1/recognition/test_audio` |
| `test listen`, `test listen-debug` | `GET /api/v1/recognition/stream` (SSE) |
| `test regression` | `POST /api/v1/regression/run` |
| `pipeline start` | `POST /api/v1/recognition/start` |
| `pipeline stop` | `POST /api/v1/recognition/stop` |
| `pipeline status` | `GET /api/v1/recognition/status` |
| `failed list` | `GET /api/v1/failed_utterances/` |
| `failed add` | `POST /api/v1/failed_utterances/` |
| `failed get` | `GET /api/v1/failed_utterances/{id}` |
| `failed update` | `PATCH /api/v1/failed_utterances/{id}` |
| `failed remove` | `DELETE /api/v1/failed_utterances/{id}` |
| `failed promote` | `POST /api/v1/failed_utterances/{id}/promote` |
| `regression list` | `GET /api/v1/regression/` |
| `regression add` | `POST /api/v1/regression/` |
| `regression get` | `GET /api/v1/regression/{id}` |
| `regression update` | `PATCH /api/v1/regression/{id}` |
| `regression remove` | `DELETE /api/v1/regression/{id}` |
| `logs list` | `GET /api/v1/logs/` (cursor-paginated: `items`/`next_cursor`/`has_more`) |
| `logs count` | `GET /api/v1/logs/count` |
| `logs get` | `GET /api/v1/logs/{id}` |
| `perf last` | `GET /api/v1/performance/last` |
| `perf summary` | `GET /api/v1/performance/summary` |
| `perf history` | `GET /api/v1/performance/history` (cursor-paginated: `items`/`next_cursor`/`has_more`) |
| `config validate` | `GET /api/v1/backup/validate` (or `POST` with `--from-dir`) |
| `config backup` | `POST /api/v1/backup/create` |
| `config backups` | `GET /api/v1/backup/list` |
| `config download` | `GET /api/v1/backup/download/{filename}` |
| `config restore` | `POST /api/v1/backup/restore/{filename}` |
| `config delete-backup` | `DELETE /api/v1/backup/{filename}` |
| `config export` | `GET /api/v1/backup/export` |
| `config import` | `POST /api/v1/backup/import` |
| `system doctor` | `GET /api/v1/health/` |
| `system devices` | `GET /api/v1/health/audio_devices` |
| `system live` | `GET /api/v1/health/live` (liveness probe) |
| `system ready` | `GET /api/v1/health/ready` (readiness probe) |
| `system info` | `GET /` |
| `system ping` | `GET /healthz` |

See the [User Guide](USER_GUIDE.md) for the REST API, configuration files and architecture.
