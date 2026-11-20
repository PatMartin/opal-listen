# `opal` CLI Reference

The `opal` command is the terminal interface to the opal listener. All recognition features live under
`opal listen`. You can use it to manage wakewords, intents, slots, profiles and settings, test recognition
with text, audio files or the microphone, review failures, and back up or restore
the configuration.

The CLI is a thin REST client. **Every command calls the opal listener REST API**. It never opens the database directly.
The server must therefore be running, either locally or on another machine.

---

## Table of contents

1. [Installation and invocation](#1-installation-and-invocation)
2. [Connecting to the server](#2-connecting-to-the-server)
3. [Conventions](#3-conventions)
4. [Command tree](#4-command-tree)
5. [`wakeword`: wakeword management](#5-wakeword--wakeword-management)
6. [`intent`: intents and training phrases](#6-intent--intents-and-training-phrases)
7. [`slot`: slots, values and aliases](#7-slot--slots-values-and-aliases)
8. [`profile`: recognition profiles](#8-profile--recognition-profiles)
9. [`persona`: OPAL personas](#9-persona--opal-personas)
10. [`setting`: global settings](#10-setting--global-settings)
11. [`test`: recognition testing](#11-test--recognition-testing)
12. [`pipeline`: microphone pipeline control](#12-pipeline--microphone-pipeline-control)
13. [`failed`: failed utterance review](#13-failed--failed-utterance-review)
14. [Recognition history](#14-recognition-history)
15. [`service perf`: performance metrics](#15-service-perf--performance-metrics)
16. [`config`: snapshots — save, load and validate](#16-config-snapshots--save-load-and-validate)
17. [`service`: health and diagnostics](#17-service--process-lifecycle-health-and-diagnostics)
18. [`event`: publish ZeroMQ events](#18-event--publish-zeromq-events)
19. [`reaction`: intent-triggered shell commands](#19-reaction--intent-triggered-shell-commands)
20. [Walkthroughs](#20-walkthroughs)
21. [Scripting and automation](#21-scripting-and-automation)
22. [Troubleshooting](#22-troubleshooting)
23. [Appendix: CLI ↔ REST mapping](#23-appendix-cli--rest-mapping)

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
opal-listen-svc               # or: python -m opal_listener
```

### Getting help

`--help` works at every level of the command tree:

```bash
opal --help                        # top level (only the 'listen' group)
opal listen --help                 # global options and all command groups
opal listen wakeword --help       # commands in one group
opal listen wakeword add --help   # arguments and options of one command
```

Running a group without a command also prints its help:

```bash
opal listen intent                # same as: opal listen intent --help
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
opal listen [--api-url URL] [--json] [--timeout SECONDS] <group> <command> [ARGS] [OPTIONS]
```

| Option | Default | Meaning |
|---|---|---|
| `--api-url URL` | `$OPAL_LISTEN_API_URL`, otherwise `http://localhost:8000` | Base URL of the REST API |
| `--json` | off | Print raw API JSON instead of tables and messages |
| `--timeout SECONDS` | `60` | HTTP timeout for each request |

```bash
# Correct: global options come right after 'listen'
opal listen --json intent list
opal listen --api-url http://pi5.local:8000 --timeout 10 service health

# Wrong: --json after the group is rejected as an unknown option (exit code 2)
opal listen intent list --json
```

### Environment variables

Set `OPAL_LISTEN_API_URL` once so you don't have to pass `--api-url` on every call. Note that this
variable has no `ER` in it, unlike the server's `OPAL_LISTENER_*` variables.

```bash
export OPAL_LISTEN_API_URL=http://192.168.1.50:8000
opal listen service health              # talks to 192.168.1.50

# One-off override for a single command
OPAL_LISTEN_API_URL=http://pi5.local:8000 opal listen pipeline status

# --api-url takes precedence over the environment variable
opal listen --api-url http://localhost:8000 service health
```

### Checking the connection

```bash
opal listen service health              # ✓ http://localhost:8000 is up
opal listen service health            # service name and version
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
opal listen intent show get_time   # by name
opal listen intent show 5          # by id
opal listen profile show 4         # profile with id 4
opal listen profile show kitchen   # profile named "kitchen"
```

Child items can also be referenced by their text:

| Item | Reference by |
|---|---|
| Training phrase | Phrase id or exact phrase text |
| Slot value | Value id or value text |
| Alias | Alias id or alias text |

```bash
opal listen phrase remove test_intent "Test intent one"   # by exact text
opal listen phrase remove test_intent 57                  # by phrase id
opal listen slot remove-alias room_name living_room lounge       # value and alias by text
```

Failed utterances and log entries have no name. You always reference them by **id**.

### Output formats

By default the CLI prints human-friendly output:

| Command kind | Default output |
|---|---|
| `list`, `values`, `aliases`, ... | A table with the most important columns |
| `show` | A key/value table |
| `add`, `update`, `remove`, ... | A one-line confirmation, e.g. `✓ wakeword 'nabu' added (id 2)` |
| `test text` / `test audio` | `MATCH` or `NO MATCH`, then a JSON summary |

With `--json`, every command prints the raw API response as JSON. This is the output to use in scripts.
Confirmations become the created or updated object, or `{"status": "ok", "message": "..."}`.

```bash
opal listen wakeword list                 # table
opal listen --json wakeword list          # JSON array, suitable for jq
opal listen --json wakeword add nabu      # JSON of the created wakeword
```

> In table output, empty values are shown as `-`, and nested lists and maps are shown as compact JSON.

### Options

- **Repeatable options.** Options such as `--phrase`, `--value`, `--alias`, `--wakeword`, `--intent`,
  `--slot`, `--threshold`, `--vad` and `--model` can be given several times. Each occurrence adds one
  item.
- **Boolean flag pairs.** Booleans use paired flags such as `--enabled/--disabled`,
  `--save/--no-save` and `--reviewed/--unreviewed`. In `update` commands, leaving out both flags keeps
  the current value.
- **`key=value` options.** `--threshold`, `--vad` and `--model` (profiles)
  take `key=value` pairs. The value is parsed as a YAML scalar: `900` becomes an integer, `0.85` a float,
  `true`/`false` a boolean, and anything else a string.
- **`update` needs at least one change.** Running `update` without any option fails with
  `Error: nothing to update (pass at least one option)`.

### Quoting

Quote multi-word arguments and every training phrase. The phrase syntax uses characters that the shell
interprets (`[`, `]`, `(`, `)`, `|`, `{`, `}`):

```bash
opal listen wakeword add "hey opal"
opal listen phrase add light_turn_on "turn on [the] {room_name} (light | lights)"
```

### Confirmation prompts

Destructive configuration commands (`config load`) ask for
confirmation. Pass `--yes` / `-y` to skip the prompt in scripts. If you answer "no", the command prints
`Aborted!` and exits with code `1`.

> `remove` commands for wakewords, intents, slots, profiles and failed utterances do
> **not** ask for confirmation. Take a snapshot first (`opal listen config save`) if you're unsure.

### Errors and exit codes

API errors are printed as `Error: HTTP <status>: <detail>`. Validation errors list each failing field:

```
Error: HTTP 422: setting 'stt_mode' must be one of grammar, open
Error: HTTP 409: wakeword 'computer' already exists
Error: 'nabu' not found in /api/v1/wakeword/
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
| `1` | API error (4xx/5xx), server unreachable, resource not found, invalid configuration, `service health` errors, `--expect-intent` mismatch, or declined confirmation |
| `2` | Invalid CLI usage: unknown option, missing argument, or an out-of-range value such as `--threshold 1.5` |

---

## 4. Command tree

`opal listen tree` launches the web console in a browser (`gui`), and prints the live command tree with each command's parameters (generated
by introspection, so it always matches the installed CLI): mandatory options print as
`--option`, optional options as `[--option]`, positional arguments as bare names.
On a terminal the tree is color-coded (root green, guides blue, groups bold cyan,
commands cyan, required parameters yellow, optional `[bracketed]` parameters dim);
piped output keeps the plain Unicode guides. `opal listen tree --ascii` prints a
color-free variant with `|--`, `` `-- `` and `|` guides for logs and non-UTF-8
environments.

```
opal
└── listen [--api-url] [--json] [--timeout]
    ├── tree [--ascii]
    ├── gui
    ├── wakeword    list | show | add | update | remove
    ├── intent      list | show | add | update | remove
    ├── phrase      list | add | remove
    ├── slot        list | show | add | update | remove | aliases | add-alias | remove-alias
    │               └── value   list | add | remove
    ├── profile     list | show | add | update | activate | remove
    ├── persona     list | show | add | update | remove | validate
    ├── test        text | audio | listen | reaction
    ├── pipeline    start | stop | status
    ├── failed      list | show | add | update | remove
    ├── service     health | start | stop | log | perf
    │               └── perf  last | summary | history
    ├── config      save | load | validate
    │               └── setting   list | show | set | remove
    └── reaction    list | show | add | update | remove | enable | disable | log | path
```

---

## 5. `wakeword`: wakeword management

A wakeword opens a command session, as in *"**computer**, turn on the kitchen light"*. Names are
normalized to lowercase without punctuation.

| Command | Description |
|---|---|
| `list` | List all wakewords |
| `show WAKEWORD` | Show one wakeword (name or id) |
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
opal listen wakeword list

# Show one wakeword by name, or by id
opal listen wakeword show computer
opal listen wakeword show 1

# Add a wakeword with a custom threshold, disabled for now
opal listen wakeword add nabu --threshold 0.7 --disabled

# Multi-word wakewords must be quoted
opal listen wakeword add "hey opal" --threshold 0.85

# Raise the threshold and enable it in one call
opal listen wakeword update nabu --threshold 0.95 --enabled

# Rename a wakeword
opal listen wakeword update "hey opal" --name "okay opal"

# Temporarily disable the default wakeword
opal listen wakeword update computer --disabled

# Delete a wakeword
opal listen wakeword remove nabu
```

> A higher threshold means fewer false triggers but more missed wakewords. A profile can raise it
> further with `--threshold wakeword=F` (see [profiles](#8-profile--recognition-profiles)).

---

## 6. `intent`: intents and training phrases

An intent is a named action, for example `light_turn_on`. Each intent has one or more **training
phrases**, which are templates the recognizer matches against the transcript.

| Command | Description |
|---|---|
| `list` | List intents with phrase counts |
| `show INTENT` | Show an intent and its phrases |
| `add NAME [--description D] [--threshold F] [--enabled/--disabled] [--phrase P]...` | Add an intent, optionally with phrases |
| `update INTENT [--name N] [--description D] [--threshold F] [--enabled/--disabled]` | Change an intent |
| `remove INTENT` | Delete an intent **and all its phrases** |

`opal listen phrase` manages an intent's training phrases:

| Command | Description |
|---|---|
| `list INTENT` | List the phrases of an intent |
| `add INTENT PHRASE` | Add one phrase |
| `remove INTENT PHRASE` | Remove a phrase by id or exact text |

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
opal listen intent list

# Show an intent: a key/value table followed by a table of its phrases
opal listen intent show get_time

# Add an intent with two literal phrases and a description
opal listen intent add test_intent \
  --phrase "Test intent one" \
  --phrase "Test intent two" \
  --description "This is a test intent"

# Add an intent using optional words and alternatives
opal listen intent add play_music \
  --description "Start music playback" \
  --threshold 0.75 \
  --phrase "play [some] music" \
  --phrase "(start | put on) [some] music"

# Add an intent that uses an existing slot
opal listen intent add light_dim \
  --description "Dim the lights in a room" \
  --phrase "dim [the] {room_name} (light | lights)"

# Change the description only
opal listen intent update test_intent --description "Updated description"

# Raise the threshold and disable the intent
opal listen intent update cancel --threshold 0.9 --disabled

# Rename an intent
opal listen intent update play_music --name music_play
```

### Managing phrases

```bash
# List phrases (columns: id, phrase, created_at)
opal listen phrase list test_intent

# Add a phrase. Always quote it.
opal listen phrase add light_turn_on "illuminate [the] {room_name}"

# Remove a phrase by its exact text...
opal listen phrase remove test_intent "Test intent one"

# ...or by its id, as shown by 'phrase list'
opal listen phrase remove test_intent 57

# Delete the whole intent, including its phrases
opal listen intent remove test_intent
```

> Use `opal listen test text "..."` right after you change phrases to check that the intent matches as
> expected.

---

## 7. `slot`: slots, values and aliases

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
| `show SLOT` | Show one slot as JSON (values and aliases included) |
| `add NAME [--type T] [--description D] [--value V]...` | Add a slot, optionally with initial values |
| `update SLOT [--name N] [--type T] [--description D]` | Change a slot |
| `remove SLOT` | Delete a slot. This fails while phrases still use it. |

| Option | Default | Meaning |
|---|---|---|
| `--type T` | `list` (on `add`) | `list`, `number` or `text` |
| `--description D` | none | Free-text description |
| `--value V` | | Initial value (repeatable, `add` only) |
| `--name N` | | Rename (on `update` only) |

### `slot value`: values and aliases

`opal listen slot value` manages a slot's canonical values; aliases stay on `slot`:

| Command | Description |
|---|---|
| `value list SLOT` | List values with their aliases |
| `value add SLOT VALUE [--alias A]...` | Add a canonical value, optionally with aliases |
| `value remove SLOT VALUE` | Remove a value (by text or id) and its aliases |
| `aliases SLOT VALUE` | List the aliases of one value |
| `add-alias SLOT VALUE ALIAS` | Add an alias to a value |
| `remove-alias SLOT VALUE ALIAS` | Remove an alias (by text or id) |

### Examples: slots

```bash
# List slots (columns: id, name, slot_type, values, description).
# Values appear as "kitchen (cooking area), living_room (lounge, family room), ..."
opal listen slot list

# Show one slot as JSON
opal listen slot show temperature

# Add a list slot with initial values
opal listen slot add color --description "Bulb colors" \
  --value red --value blue --value warm_white

# Add a number slot (number slots have no values)
opal listen slot add brightness --type number --description "Percent"

# Add a list slot with no values yet
opal listen slot add test_slot --description "Test slot"

# Change the description
opal listen slot update test_slot --description "Updated test slot"

# Delete a slot. Remove or edit the phrases that use it first.
opal listen slot remove test_slot
```

### Examples: values and aliases

```bash
# List the values of a slot (columns: id, value, aliases)
opal listen slot value list room_name

# Add a value
opal listen slot value add test_slot 42

# Add a value together with aliases
opal listen slot value add color green --alias lime --alias emerald

# Remove a value by text (its aliases are removed too)
opal listen slot value remove test_slot 42

# List the aliases of one value
opal listen slot aliases room_name living_room

# Add an alias: "martin hall" now resolves to living_room
opal listen slot add-alias room_name living_room martin_hall

# Remove the alias again
opal listen slot remove-alias room_name living_room martin_hall
```

> After you add a value or alias, run `opal listen service health`. The `vocabulary` check warns if a
> word is unknown to the speech model and therefore can't be recognized from audio.

---

## 8. `profile`: recognition profiles

A profile is an environment preset. It selects wakewords, sets threshold floors, tunes voice activity
detection (VAD), overrides model paths, and limits the enabled intents and slots. Exactly one profile is
active at a time. The seeded profiles are `default`, `noisy_room`, `car` and `kitchen`.

| Command | Description |
|---|---|
Profiles are portable documents: a `shared` object plus a `modules` map of per-module
compartments. This service reads and writes only its own compartment (`modules["opal-listen"]`)
and stores all others verbatim — a profile copied between OPAL modules keeps the other
modules' settings untouched. Editing a foreign compartment is rejected with 422.

| Command | Description |
|---|---|
| `list` | List profiles |
| `show PROFILE` | Show a profile (name or id) |
| `add [NAME] [--description D] [--is-active] [OPTIONS...]` | Add a profile |
| `update PROFILE [--name N] [--description D] [--is-active/--no-is-active] [OPTIONS...]` | Change a profile |
| `activate PROFILE` | Make this profile active (all others are deactivated) |
| `remove PROFILE` | Delete a profile |

### Portable-document options (for `add` and `update`)

| Option | Format | Meaning |
|---|---|---|
| `--in FILE` | JSON/YAML | Whole profile document (`add`) or patch document (`update`); exclusive with `--name`/`--shared`/`--module` |
| `--shared FILE` | JSON/YAML | `shared` object merged into the submitted document (must currently be `{}`) |
| `--module FILE` | JSON/YAML | The `opal-listen` compartment from a file; per-flag overrides below win on duplicates |

### Profile compartment options (for `add` and `update`)

All of these options are repeatable and write `modules["opal-listen"]` fields:

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
# List profiles (columns: id, name, is_active, then the opal-listen compartment fields
# enabled_intents, description)
opal listen profile list

# Show one profile by id or by name
opal listen profile show 4
opal listen profile show noisy_room

# Create a full profile and print the result as JSON
opal listen --json profile add test_profile \
  --description "Test Profile" \
  --wakeword nabu \
  --threshold wakeword=0.85 --threshold intent=0.8 --threshold stt=0.6 \
  --vad energy_threshold_dbfs=-35 --vad noise_margin_db=12 \
  --vad silence_end_ms=900 --vad max_utterance_ms=10000 --vad use_stt_endpoint=false \
  --intent cancel --intent set_temperature --intent get_time \
  --slot temperature

# Create a profile and activate it immediately
opal listen profile add late_night --description "Quiet evenings" --activate \
  --wakeword computer \
  --threshold wakeword=0.85 \
  --vad silence_end_ms=900 --vad energy_threshold_dbfs=-50 \
  --intent light_turn_on --intent light_turn_off --intent get_time --intent cancel \
  --slot room_name

# Change only the description (other fields are unchanged)
opal listen profile update test_profile --description "Updated Test Profile"

# Replace the VAD map. Keys you leave out return to their defaults.
opal listen profile update late_night --vad silence_end_ms=1000 --vad energy_threshold_dbfs=-50

# Use a larger Vosk model for the default profile
opal listen profile update default --model vosk_model=/models/vosk-model-en-us-0.22-lgraph

# Switch profiles
opal listen profile activate test_profile
opal listen profile activate default

# Delete a profile
opal listen profile remove test_profile
```

> You can't clear a list (for example, go back to "all intents") with `update`, because an option that
> is left out means "unchanged". To clear one, use the web GUI or the REST API, or edit the exported YAML
> and load it via `config save`/`config load` (see [config](#17-config-snapshots--save-load-and-validate)).

---

## 9. `persona`: OPAL personas

A persona is a named speaker identity registered with the service, optionally bound to an
OPAL Say `voice_id` so spoken feedback keeps a consistent voice. Personas are portable
configuration: they travel in `config save`/`config load` snapshots (`resources.personas`).

| Command | Description |
|---|---|
| `list [--enabled/--no-enabled]` | List personas, optionally filtered by enabled state |
| `show PERSONA` | Show a persona (persona_id or name) |
| `add [PERSONA_ID] --name N [--description D] [--voice-id V] [--enabled/--no-enabled] [--persona-id ID] [--if-match ETAG]` | Register a persona |
| `update PERSONA [--name N] [--description D] [--voice V] [--enabled/--no-enabled]` | Change a persona |
| `remove PERSONA` | Remove a persona |
| `validate PERSONA` | Check the persona is enabled and its declared bindings are well-formed |

### Examples

```bash
opal listen persona add amy --name "Amy" --voice en-gb-alan-low
opal listen persona list --enabled
opal listen persona validate amy
```

`--voice` names a voice in the OPAL Say catalog (e.g. `en-gb-alan-low`); this service only
checks that a declared voice id is well-formed — existence is OPAL Say's concern.

---

## 10. `config setting`: global settings

Settings are typed global key/value pairs. The server validates the type, range and allowed choices on
every change. Per the OPAL 2.0 standard, the collection `GET /api/v1/setting/` returns the flat map and
each key is addressable via `GET`/`PUT`/`DELETE /api/v1/setting/{key}`; all settings commands fetch the
current strong `ETag` and send it back as `If-Match` automatically, so updates apply safely even if
another client changed settings concurrently (a stale `If-Match` is rejected with `412`, a missing one
with `428`).

The commands live under `config setting`.

| Command | Description |
|---|---|
| `config setting list` | Show all settings with their effective values |
| `config setting show --name KEY` | Show one setting's value, registry default, `is_default`, `source` and `pending_restart` |
| `config setting set --name KEY --value VALUE` | Set one setting (`PUT /api/v1/setting/{key}`; sends the settings-map `If-Match`) |
| `config setting remove --name KEY` | Revert a setting to its registry default (`DELETE /api/v1/setting/{key}`) |

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
| `intent_dispatch` | str | `auto` | `auto`, `off`, `local`, `event`, `both` |
| `event_publication_enabled` | bool | `false` | legacy flag → `event` while `intent_dispatch=auto` |
| `reactions_enabled` | bool | `false` | legacy flag → `local` while `intent_dispatch=auto` |
| `filler_words` | str | `uh,um,er,please` | comma-separated words |
| `slot_fuzzy_min_score` | float | `0.85` | 0 to 1 |
| `token_fuzzy_min_score` | float | `0.8` | 0 to 1 |
| `audio_watchdog_timeout_ms` | int | `3000` | 500 to 60000 |
| `max_recognition_logs` | int | `10000` | 100 to 1000000 |
| `transcript_retention_days` | int | `0` | 0 to 36500; 0 = keep transcripts forever |
| `unreviewed_failure_ttl_days` | int | `90` | 0 to 36500; 0 = keep failures forever |

Booleans accept `true/false`, `1/0`, `yes/no` and `on/off`.

### Examples

```bash
# Show all settings
opal listen config setting list

# Switch the Whisper refiner model, then switch back
opal listen config setting set --name whisper_model --value medium.en
opal listen config setting set --name whisper_model --value tiny.en

# Enable the optional second-pass refiner (requires the [whisper] extra)
opal listen config setting set --name stt_refiner --value faster_whisper

# Choose the microphone by index or by name substring (see 'system devices')
opal listen config setting set --name audio_device --value 2
opal listen config setting set --name audio_device --value "USB"

# Start the microphone pipeline automatically when the server starts
opal listen config setting set --name auto_start_listener --value true

# Shorten the cooldown between commands
opal listen config setting set --name cooldown_ms --value 1000

# ... or change several settings one key at a time
opal listen config setting set --name cooldown_ms --value 1000
opal listen config setting set --name dedup_window_ms --value 3000

# Inspect one setting's metadata (default, is_default, source, pending_restart)
opal listen config setting show --name cooldown_ms

# Revert a key to its registry default
opal listen config setting remove --name cooldown_ms

# Add a sound-alike word that should never trigger the wakeword
opal listen config setting set --name wakeword_distractors --value "commuter,compute,computing,commute,commuting"

# Invalid values are rejected
opal listen config setting set --name stt_mode --value fast
# Error: HTTP 422: setting 'stt_mode' must be one of grammar, open

# Go back to the default value
opal listen config setting set --name whisper_model --value tiny.en
```

---

## 11. `test`: recognition testing

| Command | Description |
|---|---|
| `text TEXT` | Run text through normalization, intent matching and slot extraction |
| `audio WAV` | Run a WAV file through the full wakeword → VAD → STT → intent pipeline |
| `listen` | Stream live microphone results |

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

### `test listen`

```
opal listen test listen [-n COUNT] [--duration SECONDS] [--start/--no-start] [-v]
```

| Option | Default | Meaning |
|---|---|---|
| `-n`, `--count N` | `0` (unlimited) | Stop after N results |
| `--duration S` | `0` (unlimited) | Stop after S seconds |
| `--start/--no-start` | `--start` | Start the pipeline if it isn't running |
| `-v`, `--verbose` | off | Print full results |

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
opal listen test listen --count 10 --duration 10 --start
```

Example `test listen` output:

```
listening... say the wakeword (Ctrl-C to stop)
MATCH
{
  "wakeword": "computer",
  "transcript": "turn on the kitchen light",
  "intent_name": "light_turn_on",
  "slot_values": {"room_name": "kitchen"},
  "matched": true,
  "total_latency_ms": 412
}
```

Only `result` events are printed (`-v` for the full result). Debug-level stream events — audio levels,
partial transcripts, wakeword and VAD events — are still emitted by the server on
`GET /api/v1/recognition/stream?debug=true` (try `curl -N`, or the **Listen (debug)** page in the GUI).

With `--json`, each stream event is printed as `{"event": "...", "data": {...}}`. This is handy for piping
into `jq`.

---

## 12. `pipeline`: microphone pipeline control

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

> Use `opal listen config setting set --name auto_start_listener --value true` to start the pipeline every time the server
> starts.

---

## 13. `failed`: failed utterance review

When `auto_capture_failures` is `true` (the default), every `no_match` from the microphone or an audio
file is stored as a **failed utterance**. You review and annotate these.

| Command | Description |
|---|---|
| `list [--reviewed/--unreviewed] [--limit N] [--offset N]` | List failures, newest first |
| `show ID` | Show one failure |
| `add TRANSCRIPT [--expected-intent I] [--notes N] [--log-id ID]` | Record a failure manually |
| `update ID [--expected-intent I] [--notes N] [--reviewed/--unreviewed]` | Annotate a failure |
| `remove ID` | Delete a failure |

| Option | Default | Meaning |
|---|---|---|
| `--limit N` | `50` | Page size (max 500) |
| `--offset N` | `0` | Skip N rows |
| `--log-id ID` | | Link to a recognition log entry |

### Examples

```bash
# Unreviewed failures only
opal listen failed list --unreviewed

# Inspect one failure
opal listen failed show 3

# Mark a failure as reviewed and leave a note
opal listen failed update 5 --reviewed --notes "TV in background"

# Record a failure you noticed yourself, linked to log entry 42
opal listen failed add "dim the lounge" --expected-intent light_dim \
  --notes "intent does not exist yet" --log-id 42

# Delete a failure
opal listen failed remove 5
```

---

## 14. Recognition history

Every recognition (microphone, audio file, or text with `--save`) is stored in the database as a
recognition-log row; `max_recognition_logs` limits how many rows are kept and
`transcript_retention_days` controls how long transcript text stays on them (rows and latency
aggregates survive regardless). `unreviewed_failure_ttl_days` expires stale failed utterances.
The v2.0.0 contract no
longer exposes a REST surface for browsing them — use `service perf` (per-recognition latencies,
confidences and match flags, newest first) and `failed list` (captured `no_match` utterances) instead.

```bash
# Totals and latencies for recent recognitions
opal listen service perf summary
opal listen service perf history --limit 20

# Captured no_match utterances, filterable and promotable
opal listen failed list --unreviewed
```

---

## 15. `service perf`: performance metrics

| Command | Description |
|---|---|
| `last` | Latency and confidence of the most recent recognition |
| `summary [--limit N]` | Averages and percentiles over the last N recognitions (default 100, max 1000) |
| `history [--limit N] [--cursor TOKEN]` | Per-recognition rows, newest first (cursor-paginated, default 100, max 500) |

```bash
# The last recognition (prints "no recognitions yet" if there are none)
opal listen service perf last

# Match rate, average confidences, and a latency table (avg/p50/p95/max)
# for the wakeword, STT, intent and total stages
opal listen service perf summary
opal listen service perf summary --limit 1000

# Per-recognition rows (the CLI prints the --cursor to pass when has_more is true)
opal listen service perf history --limit 50
opal listen service perf history --limit 50 --cursor <token-from-previous-page>
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

## 16. `config`: snapshots — save, load and validate

The v2 contract replaces the old backup/export/import routes with OPAL configuration
snapshots: one portable envelope (`format_version`, `module`, `created_at`, `settings`,
`resources`) covering wakewords, intents, slots, profiles, personas and
settings.

| Command | Description |
|---|---|
| `validate [--in FILE]` | Validate the live configuration, or a candidate snapshot file (JSON or YAML). Exits `1` if invalid. |
| `save [--out FILE]` | Snapshot the desired configuration: replace the server's managed snapshot, or with `--out` write the envelope to a local file instead |
| `load [--in FILE] [-y]` | Atomically replace the desired configuration from the managed snapshot, or `--in FILE` for a local snapshot |

### Validation

```bash
# Validate the server's live configuration
opal listen config validate

# Validate a snapshot file before loading it
opal listen config validate --in ./snapshot.json
```

Output lists errors in red and warnings in yellow, then `configuration is valid` or
`configuration is invalid` (exit code `1`).

### Snapshots

```bash
# Capture the live configuration into the server's managed snapshot
opal listen config save

# Write a portable snapshot to a local file (leaves the managed snapshot untouched)
opal listen config save --out ./before-change.json

# Check it, then load it back. The load is atomic: a failed apply rolls everything back.
opal listen config validate --in ./before-change.json
opal listen config load --in ./before-change.json
# Replace wakewords, intents, slots, profiles, personas and settings? [y/N]: y

# Same, without the prompt (for scripts)
opal listen config load --in ./before-change.json --yes

# Restore the server's managed snapshot
opal listen config load --yes
```

`config load` fetches the live-configuration revision and sends it as `If-Match`, so a load
racing another writer fails with `412` instead of clobbering their change. A snapshot whose
`module` isn't `opal-listen` or whose `format_version` isn't `1.0.0` is rejected before
anything is touched.

> `--in` reads the snapshot on the **client** machine. Without it, `load` uses the managed
> snapshot stored on the **server** (`config save` without `--out` refreshes it).

> `load` **replaces** wakewords, intents, slots, profiles, personas and settings. Items that
> are not in the snapshot are deleted.

---

## 17. `service`: process lifecycle, health and diagnostics

`start`/`stop`/`log` are REST-first: they call the `/api/v1/service/*` ops on a running server (which
can therefore stop or restart *itself*, still returning the status first). When the API is unreachable
they fall back to the same local pidfile/log management the server-side ops use — that is what lets
`service start` cold-boot a server that isn't running yet. The managed server writes
`logs/opal_listen.log` and `logs/opal_listen.pid`.

| Command | Description |
|---|---|
| `start [--host H] [--port P] [--data-dir DIR] [--append]` | Start the managed server (replaces a running one). `--append` appends to `logs/opal_listen.log` instead of truncating. Returns immediately. |
| `stop` | Stop the managed server, if it is running |
| `log [--lines N]` | Show the last N lines of the managed server's log (default 10) |
| `perf last\|summary\|history` | Performance metrics sub-group (see [§16](#16-service-perf--performance-metrics)) |

```bash
# Run the server in the background, check it, stop it
opal listen service start --port 8000
opal listen service log
opal listen service stop

# Aggregate health report: identity, status, checks and audio devices
opal listen service health

# Standard OPAL liveness/readiness probes are REST-only (no CLI commands):
curl -s http://localhost:8000/api/v1/health/live
curl -si http://localhost:8000/api/v1/health/ready

# Audio input devices are listed in the health report's details
opal listen --json service health | jq '.details.audio_devices'
opal listen config setting set --name audio_device --value 2
```

Example `doctor` output:

```
✓ sqlite           var/opal_listener.db
✓ migrations       revision 0001 (head 0001)
✓ slot            slots compile
✓ intent          6 intents, 52 phrase variants
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

## 18. `reaction`: intent-triggered shell commands

Reactions run shell commands when a live-mic recognition matches an intent (see the User Guide §4.5
for the full model: gating, condition grammar, stdin contract).

Unlike every other group, `reaction` does **not** talk to the REST API — arming shell commands
through the API would be a remote-code-execution vector, so commands live only in
`<config_dir>/reactions.yaml`. These commands manage that file on the local filesystem (honoring
`OPAL_LISTENER_CONFIG_DIR` / `OPAL_LISTENER_DATA_DIR`); the running service picks up edits
automatically on the next match. Use `reaction path` to see which files are managed.

```bash
# Where are the managed files?
opal listen reaction path

# List armed reactions (name, enabled, #conditions, action summary)
opal listen reaction list

# Arm a reaction: fire hue.sh when "turn on the kitchen light" matches confidently
opal listen reaction add kitchen_lights \
    --cond "intent_name eq light_turn_on" \
    --cond "slot_values.room_name eq kitchen" \
    --cond "intent_confidence gte 0.8" \
    --run "sh /opt/opal/hue.sh kitchen on" \
    --timeout 15 --env HUE_BRIDGE=192.168.1.10

# Show the full reaction
opal listen reaction show kitchen_lights

# Rename it, retime every action, replace conditions wholesale
opal listen reaction update kitchen_lights --name lights_kitchen --timeout 30
opal listen reaction update lights_kitchen --cond "intent_name eq light_turn_on"
# Fire on ANY matched intent:
opal listen reaction update lights_kitchen --clear-conditions

# Disable without deleting, re-enable, disarm entirely
opal listen reaction disable lights_kitchen
opal listen reaction enable lights_kitchen
opal listen reaction remove lights_kitchen
```

`--cond` takes `"path operator value"` (operators: `eq ne gt gte lt lte in not_in exists contains`;
`exists` takes no value; values are YAML scalars, so `[a, b]` gives a list). `--run` is repeatable —
each becomes one `command` action receiving the match JSON on stdin. `--env KEY=VALUE` is repeatable.

`test reaction` dry-runs a payload against the armed reactions and shows which would fire and why
not — nothing executes unless `--execute` is passed:

```bash
# With a literal result payload
opal listen test reaction --payload '{"intent_name":"light_turn_on","intent_confidence":0.9}'

# With a real utterance through the live pipeline (needs the service running)
opal listen test reaction --text "turn on the kitchen light"

# Actually run the matching commands (debugging your scripts)
opal listen test reaction --payload '{"intent_name":"light_turn_on"}' --execute
```

`reaction log` tails the execution journal `<data_dir>/reaction_log.jsonl` (one JSON record per run:
timestamp, argv, exit code, duration, stderr tail). `--limit N` controls how many rows print; the
`reactions_fired` / `reactions_failed` / `reactions_dropped` counters also appear in
`pipeline status` metrics.

```bash
opal listen reaction log --limit 5
```

Remember: armed reactions only fire when the dispatch policy selects `local` — `opal listen config
setting set --name intent_dispatch --value local` (or `both`). With the default `intent_dispatch=auto` the legacy
`reactions_enabled true` flag selects the same target. The switch is a setting so it can be flipped
at runtime without editing files.

---

## 19. Walkthroughs

### 20.1 Full CRUD tour (based on `scripts/test-cli.sh`)

This tour exercises every catalog group and cleans up after itself. You can run it on a fresh server.

```bash
# --- sanity -------------------------------------------------------------------------------
opal listen service health
opal listen service health

# --- wakewords ----------------------------------------------------------------------------
opal listen wakeword list
opal listen wakeword show computer
opal listen wakeword add nabu --threshold 0.7 --disabled     # created disabled
opal listen wakeword update nabu --threshold 0.95 --enabled   # now enabled, stricter
opal listen wakeword show nabu

# --- intents and phrases ------------------------------------------------------------------
opal listen intent add test_intent \
  --phrase "Test intent one" --phrase "Test intent two" \
  --description "This is a test intent"
opal listen intent update test_intent --description "Updated description"
opal listen phrase list test_intent
opal listen phrase remove test_intent "Test intent one"
opal listen phrase list test_intent                        # one phrase left

# --- slots, values and aliases ------------------------------------------------------------
opal listen slot add test_slot --description "Test slot"
opal listen slot update test_slot --description "Updated test slot"
opal listen slot value add test_slot 42
opal listen slot value list test_slot
opal listen slot value remove test_slot 42
opal listen slot aliases room_name living_room
opal listen slot add-alias room_name living_room martin_hall
opal listen slot aliases room_name living_room                # now includes martin_hall
opal listen slot remove-alias room_name living_room martin_hall

# --- profiles -----------------------------------------------------------------------------
opal listen --json profile add test_profile \
  --description "Test Profile" \
  --wakeword nabu \
  --threshold wakeword=0.85 --threshold intent=0.8 --threshold stt=0.6 \
  --vad energy_threshold_dbfs=-35 --vad noise_margin_db=12 \
  --vad silence_end_ms=900 --vad max_utterance_ms=10000 --vad use_stt_endpoint=false \
  --intent cancel --intent set_temperature --intent get_time \
  --slot temperature
opal listen profile update test_profile --description "Updated Test Profile"
opal listen profile activate test_profile
opal listen profile activate default                          # switch back before removing

# --- settings -----------------------------------------------------------------------------
opal listen config setting list
opal listen config setting set --name whisper_model --value medium.en
opal listen config setting set --name whisper_model --value tiny.en

# --- recognition --------------------------------------------------------------------------
opal listen test text "computer, turn on the kitchen light"
opal listen test audio resources/cmds/cmd-kitchen-lights-off.wav

# --- pipeline -----------------------------------------------------------------------------
opal listen pipeline status
opal listen pipeline start
opal listen test listen --count 10 --duration 10 --no-start
opal listen pipeline stop

# --- clean up -----------------------------------------------------------------------------
opal listen intent remove test_intent
opal listen slot remove test_slot
opal listen profile remove test_profile
opal listen wakeword remove nabu
```

### 20.2 Add a new intent with a new slot

```bash
# 1. Create the slot and its values
opal listen slot add color --description "Bulb colors" --value red --value blue --value warm_white
opal listen slot add-alias color warm_white cozy

# 2. Create the intent with phrases that use the slot
opal listen intent add light_color \
  --description "Change bulb color" \
  --phrase "make [the] {room_name} (light | lights) {color}" \
  --phrase "set [the] {room_name} (light | lights) to {color}"

# 3. Try it
opal listen test text "computer, set the kitchen lights to cozy"
# -> light_color, {"room_name": "kitchen", "color": "warm_white"}

# 4. Check that the speech model knows the new words
opal listen service health
```

### 20.3 The review and tuning loop

```bash
# 1. Use the system for a while, or listen live
opal listen test listen --duration 300

# 2. Look at what failed
opal listen service perf summary
opal listen failed list --unreviewed

# 3. Teach real commands that should have matched
opal listen phrase add light_turn_on "turn on the {room_name} light"

# 4. Mark noise as reviewed so it doesn't show up again
opal listen failed update 13 --reviewed --notes "background TV"

# 5. Check latency and match rate
opal listen service perf summary
```

### 20.4 Tune for a noisy room

```bash
# 1. Watch the levels while the room is noisy (but nobody is speaking)
opal listen test listen --duration 20

# 2. Activate the noisy_room profile, or tune it
opal listen profile update noisy_room \
  --threshold wakeword=0.9 --threshold intent=0.8 \
  --vad energy_threshold_dbfs=-38 --vad noise_margin_db=14 --vad silence_end_ms=600
opal listen profile activate noisy_room

# 3. Test again
opal listen test listen -n 5
```

### 20.5 Back up and move configuration to another machine

```bash
# On the source machine
opal listen config save --out ./opal-config/snapshot.json
git -C ./opal-config init && git -C ./opal-config add . && git -C ./opal-config commit -m "opal config"

# Against the target machine
export OPAL_LISTEN_API_URL=http://new-pi.local:8000
opal listen config validate --in ./opal-config/snapshot.json
opal listen config load --in ./opal-config/snapshot.json --yes
```

---

## 20. Scripting and automation

### JSON output with `jq`

```bash
# Names of all intents
opal listen --json intent list | jq -r '.[].name'

# Enabled wakewords and their thresholds
opal listen --json wakeword list | jq -r '.[] | select(.enabled) | "\(.name)\t\(.confidence_threshold)"'

# Only the intent and slots of a text test
opal listen --json test text "set the temperature to twenty one degrees" --no-save \
  | jq '{intent_name, slot_values}'

# The currently active profile
opal listen --json profile list | jq -r '.[] | select(.is_active) | .name'

# Number of unreviewed failures
opal listen --json failed list --unreviewed --limit 500 | jq length

# Stream live results as JSON (one object per result; jq -c makes it one line each)
opal listen --json test listen | jq -c '{intent_name, slot_values}'
```

### Shell assertions

```bash
#!/usr/bin/env bash
set -euo pipefail

opal listen service health
opal listen test text "computer, turn on the kitchen light" --no-save --expect-intent light_turn_on
opal listen test text "order a pizza"                       --no-save --expect-intent no_match
opal listen test audio resources/cmds/cmd-cancel.wav        --no-save --expect-intent cancel
echo "all checks passed"
```

### CI: guard your configuration

```bash
# Fails the job (exit 1) if the snapshot is invalid
opal listen config validate --in ./opal-config/snapshot.json
opal listen config load --in ./opal-config/snapshot.json --yes
```

### Bulk operations

```bash
# Add many values to a slot
for room in attic basement porch; do
  opal listen slot value add room_name "$room"
done

# Add a phrase file line by line
while IFS= read -r phrase; do
  opal listen phrase add light_turn_on "$phrase"
done < phrases.txt

# Disable all intents whose name starts with "wip_"
opal listen --json intent list \
  | jq -r '.[] | select(.name | startswith("wip_")) | .id' \
  | xargs -r -n1 opal listen intent update --disabled
```

> `xargs -n1` appends the id after `--disabled`. That works because options may come before positional
> arguments.

---

## 21. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Error: cannot reach the opal listener API at ...` | The server isn't running, or the URL is wrong. Start `opal-listen-svc`, or set `OPAL_LISTEN_API_URL` / `--api-url`. |
| `No such option: --json` | Global options must come right after `listen`: `opal listen --json intent list`. |
| `Error: 'xyz' not found in /api/v1/...` | No resource with that name or id exists. Check with `list`. Names are normalized, so try lowercase. |
| `Error: nothing to update (pass at least one option)` | `update` was called without any change option. |
| `Error: HTTP 422: ...` | The server rejected the input (bad phrase syntax, unknown slot, value out of range). The message names the field. |
| `Error: HTTP 409: ...` | A resource with that name already exists. |
| `slot remove` fails | Phrases still use the slot. Remove or edit those phrases first. |
| Profile `update` lost a list entry | List and map options replace the stored value. Pass the full set. |
| `test audio` returns `no_wakeword` | You used `--require-wakeword` and the recording has no audible wakeword. Check with `-v`. |
| `test listen` shows nothing | Check `service health`, `config setting list` (look at `audio_device`) and `pipeline status`. Debug-level stream events (audio levels, partials) are available via `GET /api/v1/recognition/stream?debug=true`. |
| Commands get cut off | Raise `silence_end_ms` in the active profile, or set `use_stt_endpoint=false`. |
| False wakeword triggers | Raise the wakeword threshold, add sound-alikes to `wakeword_distractors`, or set `wakeword_trigger final`. |
| `vocabulary` warning in `doctor` | A phrase or alias uses a word the speech model doesn't know. Rephrase it or use a larger model. |
| Long operations time out | Increase `--timeout`, e.g. `opal listen --timeout 300 config load --in snapshot.json -y`. |

---

## 22. Appendix: CLI ↔ REST mapping

All paths are relative to the API base URL. Commands that take a name or id first resolve it with a
`GET` on the collection. Per the OPAL v1.2 standard, every `PATCH`/`DELETE` below on wakewords, intents,
phrases, slots, slot values, aliases, profiles, personas and settings requires a strong `If-Match`; the CLI fetches
the current `ETag` automatically before sending the mutation (missing precondition → `428`, stale → `412`).

| CLI (`opal listen ...`) | REST |
|---|---|
| `wakeword list` | `GET /api/v1/wakeword/` |
| `wakeword add` | `POST /api/v1/wakeword/` |
| `wakeword show` | `GET /api/v1/wakeword/{id}` |
| `wakeword update` | `PATCH /api/v1/wakeword/{id}` (If-Match) |
| `wakeword remove` | `DELETE /api/v1/wakeword/{id}` (If-Match) |
| `intent list` | `GET /api/v1/intent/` |
| `intent add` | `POST /api/v1/intent/` |
| `intent show` | `GET /api/v1/intent/{id}` |
| `intent update` | `PATCH /api/v1/intent/{id}` (If-Match) |
| `intent remove` | `DELETE /api/v1/intent/{id}` (If-Match) |
| `phrase list` | `GET /api/v1/intent/{id}/phrase` |
| `phrase add` | `POST /api/v1/intent/{id}/phrase` |
| `phrase remove` | `DELETE /api/v1/intent/{id}/phrase/{phrase_id}` (If-Match) |
| `slot list` | `GET /api/v1/slot/` |
| `slot add` | `POST /api/v1/slot/` (+ `POST .../value` per `--value`) |
| `slot show` | `GET /api/v1/slot/{id}` |
| `slot update` | `PATCH /api/v1/slot/{id}` (If-Match) |
| `slot remove` | `DELETE /api/v1/slot/{id}` (If-Match) |
| `slot value list` | `GET /api/v1/slot/{id}/value` |
| `slot value add` | `POST /api/v1/slot/{id}/value` (+ `POST .../alias` per `--alias`) |
| `slot value remove` | `DELETE /api/v1/slot/{id}/value/{value_id}` (If-Match) |
| `slot aliases` | `GET /api/v1/slot/{id}/value/{value_id}/alias` |
| `slot add-alias` | `POST /api/v1/slot/{id}/value/{value_id}/alias` |
| `slot remove-alias` | `DELETE /api/v1/slot/{id}/value/{value_id}/alias/{alias_id}` (If-Match) |
| `profile list` | `GET /api/v1/profile/` |
| `profile add` | `POST /api/v1/profile/` |
| `profile show` | `GET /api/v1/profile/{id}` |
| `profile update` | `PATCH /api/v1/profile/{id}` (If-Match) |
| `profile remove` | `DELETE /api/v1/profile/{id}` (If-Match) |
| `profile activate` | `POST /api/v1/profile/{id}/activate` |
| `persona list` | `GET /api/v1/persona/` |
| `persona add` | `POST /api/v1/persona/` |
| `persona show` | `GET /api/v1/persona/{persona_id}` |
| `persona update` | `PATCH /api/v1/persona/{persona_id}` (If-Match) |
| `persona remove` | `DELETE /api/v1/persona/{persona_id}` (If-Match) |
| `persona validate` | `POST /api/v1/persona/{persona_id}/validate` |
| `config setting list` | `GET /api/v1/setting/` |
| `config setting show` | `GET /api/v1/setting/{key}` |
| `config setting set` | `PUT /api/v1/setting/{key}` (If-Match) |
| `config setting remove` | `DELETE /api/v1/setting/{key}` (If-Match) |
| `test text` | `POST /api/v1/recognition/test_text` |
| `test audio` | `POST /api/v1/recognition/test_audio` |
| `test listen` | `GET /api/v1/recognition/stream` (SSE) |
| `pipeline start` | `POST /api/v1/recognition/start` |
| `pipeline stop` | `POST /api/v1/recognition/stop` |
| `pipeline status` | `GET /api/v1/recognition/status` |
| `failed list` | `GET /api/v1/failed_utterance/` |
| `failed add` | `POST /api/v1/failed_utterance/` |
| `failed show` | `GET /api/v1/failed_utterance/{id}` |
| `failed update` | `PATCH /api/v1/failed_utterance/{id}` |
| `failed remove` | `DELETE /api/v1/failed_utterance/{id}` |
| `service start` | `POST /api/v1/service/start` (falls back to local spawn when the API is unreachable) |
| `service stop` | `POST /api/v1/service/stop` (falls back to local pidfile stop when unreachable) |
| `service log` | `GET /api/v1/service/log` (falls back to the local log file when unreachable) |
| `service perf last` | `GET /api/v1/performance/last` |
| `service perf summary` | `GET /api/v1/performance/summary` |
| `service perf history` | `GET /api/v1/performance/history` (cursor-paginated: `items`/`next_cursor`/`has_more`) |
| `config validate` | `POST /api/v1/config/validate` (live, or `{snapshot}` with `--in`) |
| `config save` | `POST /api/v1/config/save` (`managed`, or `response` with `--out`) |
| `config load` | `POST /api/v1/config/load` (If-Match; managed, or `{snapshot}` with `--in`) |
| `service health` | `GET /api/v1/health/` |

See the [User Guide](USER_GUIDE.md) for the REST API, configuration files and architecture.
