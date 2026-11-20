# OPAL Listen Quickstart

## Overview

OPAL Listen turns a wakeword and a spoken command into an **intent** (the requested
action) and **slots** (parameters such as a room or number of minutes). Recognition
runs locally with Vosk. Use it to build voice interfaces without a cloud speech
service.

This guide starts with real text-to-intent recognition, without a microphone,
speech model, or another OPAL module. Then you will customize commands, save and
restore configuration, replay a supplied WAV, and optionally use a microphone or
second-pass speech engine. The first text match should take less than five minutes
after installation.

Recognition alone does not operate a light or start a timer. Local command
**reactions** and publishing **events** to OPAL Event are separate, opt-in actions.
Ordinary text and WAV diagnostics do not fire reactions or publish intent events.

## Prerequisites

**Required for the initial text workflow**

- Linux is the primary target; Ubuntu/Debian and 64-bit Raspberry Pi OS are the
  documented deployment choices. CI runs on Ubuntu with Python 3.11, 3.12 and
  3.13. The package declares Python `>=3.11`, but that is not evidence that every
  newer Python or other operating system has been tested.
- Python 3.11-3.13, its `venv` support, pip, Git, and an internet connection for
  obtaining the source and dependencies.
- A writable working directory for SQLite data, logs and optional snapshots.

**Optional**

- **WAV recognition:** an extracted Vosk model. No microphone or PortAudio device
  is needed. The small US-English model is about a 40 MB download and needs
  roughly 300 MB of runtime memory, according to the
  [Vosk model catalog](https://alphacephei.com/vosk/models).
- **Live recognition:** a working microphone/input device and the PortAudio
  runtime (`libportaudio2` on Debian-family Linux). The Python `sounddevice`
  library is already a base dependency.
- **Refinement:** the `whisper` package extra and a faster-whisper model; see
  [Engines and plugins](#engines-and-plugins).
- **Automation:** OPAL Event only if you choose external event delivery. No
  other OPAL service is needed for the examples below.

Windows and macOS instructions are not validated here. The commands below use
Bash and Linux paths.

## Installation

On Debian/Ubuntu/Raspberry Pi OS, install the system prerequisites. PortAudio is
included here so the same environment is ready for the optional microphone step:

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip libportaudio2
```

Ensure your selected `python3` is one of the versions above. Obtain the source,
create and activate a virtual environment, then install the module and its base
dependencies:

```bash
git clone https://github.com/PatMartin/opal-listen.git
cd opal-listen
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
opal --help
opal listen --help
opal-listen-svc --help
```

This is a source install, not an assumption that the project is published on
PyPI. The distribution is named `opal-listener`; it installs the `opal` CLI and
`opal-listen-svc` service executable. The `dev` extra is unnecessary for normal
use.

Keep using the repository root as the working directory. Relative data/log paths
and the supplied audio examples below depend on it.

## Initial configuration

No manual database initialization or catalog import is needed. On first startup,
the service creates/migrates SQLite and seeds an empty database from the packaged
[YAML configuration](../opal_listener/config/). Defaults include:

| Item | Default |
| --- | --- |
| Wakeword | `computer` |
| Intents | `light_turn_on`, `light_turn_off`, `set_temperature`, `timer_set`, `get_time`, `cancel` |
| Slots | `room_name`, `temperature`, `duration_minutes` |
| Profiles | Active `default`; also `noisy_room`, `car`, `kitchen` |
| Speech engine / mode / refiner | `vosk` / `grammar` / `none` |
| Microphone auto-start | `false` |
| External events / local reactions | Both disabled by default |

### Process settings and dotenv

| Environment variable | Default / purpose |
| --- | --- |
| `OPAL_LISTENER_HOST` | `0.0.0.0` for the foreground executable; managed `service start` defaults to `127.0.0.1` |
| `OPAL_LISTENER_PORT` | `8000` |
| `OPAL_LISTEN_API_URL` | CLI target, `http://localhost:8000` (**no `ER`**) |
| `OPAL_LISTENER_DATA_DIR` | `var`, relative to the service working directory |
| `OPAL_LISTENER_DATABASE_FILE` | `opal_listener.db` inside the data directory |
| `OPAL_LISTENER_CONFIG_DIR` | The installed package's `config` directory; seeds an empty database |
| `OPAL_LISTENER_VOSK_MODEL_PATH` | `/resources/vosk/models/vosk-model-small-en-us-0.15`; usually must be changed for audio |
| `OPAL_LISTENER_API_TOKEN` | Unset: authentication disabled. When set, normal CLI requests send this same token. |
| `OPAL_LISTENER_MAX_UPLOAD_BYTES` | `10485760` (10 MiB) for uploaded WAVs |

CLI and service startup normally read `.env` in the current directory and create
a baseline file if it is absent. Existing environment variables win, including
empty values. See the [dotenv template](../.env.example). This guide explicitly
disables dotenv so an old local file cannot change the examples:

```bash
export OPAL_NO_DOTENV=1
export OPAL_LISTENER_HOST=127.0.0.1
export OPAL_LISTEN_API_URL=http://127.0.0.1:8000
```

Set these in every terminal you use. For persistent dotenv configuration instead,
remove `OPAL_NO_DOTENV` from your environment and edit `.env`. Both executables
also support `--env-file`; the `opal` CLI takes that option after `listen`.

The examples assume a fresh local database and no existing service on port 8000.
Do not run catalog edits or restores against a production installation. If you
choose another port, set the CLI URL accordingly and pass the same port to
`service start --port`.

Keep this first run on loopback. The foreground executable otherwise binds all
interfaces. Configure authentication and network protection before exposing it
outside your machine; see the lifecycle authentication limitation under
[Troubleshooting](#troubleshooting).

## Starting the module

Start a managed background server:

```bash
opal listen service start
opal listen service health
opal listen pipeline status
```

The start result reports a PID and log location. If the service is still
initializing, repeat `service health` after a few seconds. The health report should be
`ok` (or `degraded`). The pipeline should show `running: false`: starting the service
does **not** start the microphone by default.

The managed process writes `logs/opal_listen.log` and `logs/opal_listen.pid`.
Use the same working directory to manage it. Inspect startup errors with:

```bash
opal listen service log --lines 20
```

Alternatively, run the foreground executable in a dedicated terminal:

```bash
opal-listen-svc
```

Use **one** startup method, not both on the same port. Ctrl-C stops a foreground
server. Managed stop/restart commands are shown at the end of this guide.

## First working example: recognize a command without hardware

With the service running, recognize the seeded kitchen-light command:

```bash
opal listen test text "computer, turn on the kitchen light" \
  --expect-intent light_turn_on
```

Expect `MATCH`, exit status 0, and a JSON summary containing
`intent_name: "light_turn_on"`, `slot_values: {"room_name": "kitchen"}` and
`matched: true`. Confidence, timing and identifiers are not fixed values.
`--expect-intent` makes an unexpected intent fail with exit status 1.

This exercises normalization, wakeword stripping, intent matching and slot
extraction, **not speech-to-text**. Text tests also accept commands without a
wakeword. They do not need a Vosk model, even though Vosk remains the selected
speech provider.

For machine-readable output, global options go immediately after `listen`:

```bash
opal listen --json test text "set a timer for five minutes" \
  --expect-intent timer_set
opal listen service perf last
```

Verify `duration_minutes` is the number `5`. The performance command shows the
stored measurement for the last recognition. Diagnostic tests save logs by
default; add `--no-save` when you do not want them retained. Transcripts are
retained indefinitely by default (`transcript_retention_days=0`).

## Additional examples

Run these in order on the same local service. Each example is hardware-independent.

### 1. Inspect and protect your starting configuration

Purpose: discover the seeded catalog and create a rollback point before editing.

```bash
opal listen wakeword list
opal listen intent list
opal listen slot value list room_name
opal listen profile list
opal listen config validate
opal listen config save --out ./quickstart-before.json
opal listen config validate --in ./quickstart-before.json
```

Expect the defaults listed above, `configuration is valid`, and a local
`quickstart-before.json` snapshot envelope. Validating that file verifies it was
written and can be read back. Validation checks configuration, not microphone
availability or successful model loading.

Snapshots contain desired settings overrides, profiles, personas, wakewords,
intents and slots. They exclude secrets, audio/models,
recognition history and the separate reactions file. Without `--out`, `config
save` replaces the server's single managed snapshot in the data directory,
`managed_config_snapshot.json`; it is not a full database backup.

### 2. Add a phrase and a room alias

Purpose: teach a new expression and normalize a nickname to a canonical slot
value, without acoustic training.

```bash
opal listen intent add quickstart_music \
  --description "Quickstart music request" --phrase "play some music"
opal listen phrase list quickstart_music
opal listen test text "computer, play some music" --expect-intent quickstart_music
opal listen slot add-alias room_name office "the den"
opal listen test text "computer, turn on the den light" \
  --expect-intent light_turn_on
```

Verify the first test matches `quickstart_music`; the second returns
`room_name: "office"`, not `"the den"`. Adding an intent does not play music.
Names must be unique; a second run of the add commands against the same database
can report a conflict. Restore the initial snapshot before repeating the guide.

### 3. Make recognition expectations repeatable

Purpose: catch future vocabulary changes that break an intent or its slots.

```bash
opal listen test text "computer, turn on the kitchen light" --expect-intent light_turn_on --no-save
opal listen test text "computer, set a timer for five minutes" --expect-intent timer_set --no-save
opal listen test text "order a pizza" --expect-intent no_match --no-save
```

Each command prints `MATCH` and exits 0; `--expect-intent` exits 1 when the text
resolves to a different intent, so the same checks can guard a script. These are
text tests, not WAV or microphone tests.

### 4. Change a setting and select an environment profile

Purpose: use the implemented OPAL configuration CLI and inspect effective
selection.

```bash
opal listen config setting show --name cooldown_ms
opal listen config setting set --name cooldown_ms --value 800
opal listen config setting show --name cooldown_ms
opal listen profile activate kitchen
opal listen --json profile show kitchen
opal listen test text "computer, turn on the kitchen light" \
  --expect-intent light_turn_on
opal listen profile activate default
opal listen config setting remove --name cooldown_ms
opal listen config setting show --name cooldown_ms
```

Verify the setting first changes from `1500` to `800`, then returns to its default
`1500` when the override is removed. `cooldown_ms` affects live wakeword sessions,
not the speed of text tests.

The selected profile reports `is_active: true`; its Listen-specific fields are
under `modules["opal-listen"]`, alongside the portable `shared` compartment.
The kitchen profile permits a subset of intents/slots; activating `default`
restores the unrestricted seeded profile. Thresholds on profiles are minimum
floors, not replacements for higher per-intent thresholds.

Most runtime edits hot-apply; `auto_start_listener` is startup-only. Its setting
metadata reports `restart_required` and `pending_restart`. Do not assume every
setting takes effect without a restart. YAML catalog edits do not automatically
replace a database that has already been seeded; use the CLI for ongoing edits.

### 5. Restore the starting configuration

Purpose: undo the additions above without deleting recognition history.

**This replaces the current desired configuration**.
Only run it against the installation you used for these examples:

```bash
opal listen config load --in ./quickstart-before.json --yes
opal listen intent list
opal listen config validate
```

Verify `quickstart_music` is gone and configuration is valid. The slot alias and setting/profile changes are restored
too. Omitting `--yes` prompts for confirmation. A snapshot is specific to its
module/format; an arbitrary YAML seed file is not a snapshot.

## Hardware-independent audio: replay a WAV with Vosk

Purpose: test actual wakeword detection and speech-to-text, using the default
engine rather than simulated recognition.

1. Download [vosk-model-small-en-us-0.15.zip](https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip)
   from the official catalog and extract it under your home directory's `models`
   folder. The resulting directory should be
   `$HOME/models/vosk-model-small-en-us-0.15`, containing `am/` and `conf/`.
   The module has **no CLI command to download or unpack models**.
2. Set the stored model path through the CLI. This works even if the service was
   started before you obtained the model:

```bash
opal listen config setting set --name vosk_model_path \
  --value "$HOME/models/vosk-model-small-en-us-0.15"
opal listen config setting set --name listener_provider --value vosk
opal listen config setting show --name listener_provider
opal listen service health
opal listen test audio \
  opal_listener/tests/audio/fixtures/golden_kitchen_light_on.wav \
  --require-wakeword --expect-intent light_turn_on
opal listen service perf last
```

Verify the provider is `vosk`, the health checks show the model/provider loaded,
and the WAV result has `wakeword: "computer"`, `intent_name: "light_turn_on"` and
`room_name: "kitchen"`. Readiness can be `degraded` solely because no microphone
exists; that does not prevent WAV testing. A missing model causes health/readiness
errors but does not prevent the earlier text examples.

The WAV fixture is included in the source checkout, not installed as package
data. WAV input must be **16-bit PCM**, mono or stereo, 8-96 kHz; the service
converts it to 16 kHz mono. Compressed audio and MP3 are not accepted here.

Test a command-only recording that must not trigger without its wakeword:

```bash
opal listen test audio \
  opal_listener/tests/audio/fixtures/nowake_command_only.wav \
  --require-wakeword --expect-intent no_match
```

Expect `NO MATCH`, `intent_name: "no_match"` and exit status 0, because that is the
explicit expectation. `NO MATCH` alone is not a CLI failure. Without
`--expect-intent`, an unsuccessful recognition can still exit 0.

## Real hardware: recognize the same command through a microphone

Prerequisites: the Vosk setup above, PortAudio and a working input device attached
to the **service machine**. The CLI streams that machine's microphone results;
it does not capture your client's microphone remotely.

```bash
opal listen service health
opal listen config setting show --name audio_device
opal listen test listen --count 1 --duration 30 --verbose
opal listen pipeline status
```

The health output lists available input devices. The default `audio_device` is an
empty string, meaning the system default input. During the listening command say
**"computer, turn on the kitchen light"**. Expect the same intent and kitchen slot
as the WAV test, with `source: "mic"` in verbose output.

`--count 1` stops after one result, not necessarily one successful match. Ctrl-C
also ends the CLI session. If the command started the pipeline, it stops it on
exit; a pipeline that was already running is left running. The duration limit is
checked as stream data arrives, so Ctrl-C is the reliable manual stop.

To select a device, use an index from your health report or an actual unique
substring of its name as the `audio_device` setting value. Device indices are
machine-specific; do not copy someone else's index. For capture problems, return
to the system default and inspect the live stream's debug events:

```bash
opal listen config setting set --name audio_device --value ""
curl -N "http://localhost:8000/api/v1/recognition/stream?debug=true"
```

Expect audio-level/status events and, when speech is recognized, partial/final
results. No changing levels suggests an input/permission/device problem, rather
than an intent-template problem.

For a continuously running pipeline, control its lifecycle separately:

```bash
opal listen pipeline start
opal listen pipeline status
opal listen test listen --no-start --count 1 --duration 30
opal listen pipeline stop
opal listen pipeline status
```

Verify `running: true` after start and `running: false` after stop. `pipeline stop`
stops capture, not the service. Inspect `last_error` as well as `running` if no
results arrive.

## Engines and plugins

### Default engine and discovery limitations

The speech engine setting is `listener_provider`; the shipped providers are
`vosk` and `fake`. `fake` is a deterministic development/test provider, **not**
an alternative speech recognizer. Its scripted transcripts cannot be supplied
through this public CLI.

```bash
opal listen config setting list
opal listen config setting show --name listener_provider
opal listen config setting show --name stt_mode
opal listen config setting show --name stt_refiner
```

The defaults are `vosk`, `grammar`, `none`. The WAV example above verifies the
default engine. `stt_mode` accepts `grammar` (intent-constrained vocabulary) or
`open` (dictation). Free-text slots require open vocabulary.

There is **no engine/plugin inventory or plugin-install CLI** in this module.
Third-party listener providers register the Python entry-point group
`opal_listener.providers`; package installation is outside the module CLI.
After installing a compatible provider into the service environment, restart the
service, set `listener_provider` to its registered name with `config setting set`,
and verify it with `service health` and a real audio test. Do not assume a generic
shared-contract engine command exists here. See the
[provider architecture](USER_GUIDE.md#32-components) and
[registry implementation](../opal_listener/service/providers/registry.py).

### Optional faster-whisper refinement

This is a second pass after Vosk, not a replacement listener provider.
Prerequisites: the working Vosk/WAV setup, extra disk/memory, and internet access
for an uncached `tiny.en` model. Install the extra into the same virtual environment
and restart the managed service before selecting it:

```bash
python -m pip install -e ".[whisper]"
opal listen service start --append
opal listen service health
opal listen config setting set --name whisper_model --value tiny.en
opal listen config setting set --name whisper_compute_type --value int8
opal listen config setting set --name stt_refiner --value faster_whisper
opal listen config setting show --name stt_refiner
opal listen --timeout 180 test audio \
  opal_listener/tests/audio/fixtures/golden_kitchen_light_on.wav --verbose
opal listen config setting set --name stt_refiner --value none
```

Wait for the restart before continuing. Expect `stt_refiner: faster_whisper` and
an audio recognition result after the optional model loads; inspect the
transcript/intent rather than expecting fixed timing. First use may download a
model and take longer than subsequent calls. Text tests and model-path checks
alone do not verify refinement. For live audio, inspect `refinement_error` and
the pipeline's refinement metrics: timeout/failure can preserve the draft result
instead of proving that refinement succeeded. The final command disables the
optional pass again.

## Optional automation and further CLI discovery

Local reactions execute configured commands on live matched intents without
OPAL Event. Before managing any, inspect their locations and current contents:

```bash
opal listen reaction path
opal listen reaction list
opal listen test reaction --help
```

Reaction commands manage the **CLI machine's local** configuration file and
execution journal, not a remote server's files. Reactions are outside configuration
snapshots; protect that file separately. Ordinary diagnostic tests do not dispatch
actions, but the explicit `test reaction --execute` operation **does execute
commands**. Inspect configured actions before using it.

For external intent delivery, OPAL Event must be running and configured using
`OPAL_EVENT_API_URL` (default `http://127.0.0.1:8080`), optionally
`OPAL_EVENT_API_TOKEN`. `OPAL_EVENT_TRANSPORT` selects `http` or `zmq`; transport
`both` is rejected. These are startup environment settings, unlike stored
recognition settings. The default `intent_dispatch=auto` uses the initially-off
`event_publication_enabled` and `reactions_enabled` switches. Do not enable them
until receivers/actions are ready.

Discover more supported operations without guessing commands:

```bash
opal listen tree --ascii
opal listen failed --help
opal listen persona --help
opal listen service perf summary
```

The tree is generated from the installed CLI. Failed-utterance review supports
manual records and promotion to phrases. Text `no_match`
diagnostics are **not** automatically added to the failed-utterance queue;
audio/live failures can be. Personas provide optional local identity metadata
and do not require another module for local CRUD.

## Stopping and restarting

For the managed process, run these from the same directory used to start it:

```bash
opal listen service stop
opal listen service log --lines 10
```

Expect `server stopped`. Logs remain readable using the local fallback after
shutdown. Allow a moment for the process to exit before reusing its port.

To start it again:

```bash
opal listen service start --append
opal listen service health
```

`service start` also **restarts an already managed server**; there is no separate
`service restart` command. `--append` preserves previous log contents; without
it, startup truncates the log. On a cold start, `--host`, `--port` and `--data-dir`
select the local launch configuration. When contacting a running service,
`service start` restarts it with its existing environment; those local-launch
flags do not reconfigure that server. Stop it first to change them.

When finished:

```bash
opal listen service stop
```

For the foreground alternative, use Ctrl-C in its terminal instead. Do not
assume managed stop controls an untracked foreground server.

## Troubleshooting

| Problem | Possible cause | Resolution |
| --- | --- | --- |
| `opal` not found | Wrong shell/environment or project not installed | Activate this checkout's virtual environment and repeat the source installation. |
| Dependency install or `venv` fails | Unsupported Python or missing OS venv support | Use Python 3.11-3.13 and install the matching OS venv/pip packages. |
| CLI cannot reach service | Service stopped, still starting, crashed, or wrong URL | Start it, retry `service live`, check `service log`, and align `OPAL_LISTEN_API_URL` with the selected port. |
| Address already in use | Another server owns the port | Do not start both foreground and managed servers on one port. Stop only your own server or choose another port and CLI URL. |
| Text works but health/readiness fails | Vosk model missing | Configure the extracted model path as in the WAV example. Liveness is not a model-readiness check. |
| Changing the model environment variable has no effect | SQLite already stores a model path, or a profile overrides it | Set `vosk_model_path` with the CLI; inspect the active profile's `modules["opal-listen"].model_paths.vosk_model`. |
| `not a Vosk model` / missing `am/` or `conf/` | Path points at a ZIP or its parent directory | Select the extracted model directory itself, readable by the service user. |
| `NO MATCH` yet exit status is 0 | A recognition miss is a valid diagnostic result | Use `--expect-intent` to enforce the expected result; inspect verbose output, enabled intents/slots and active profile. |
| HTTP 413 / invalid WAV | Upload too large or unsupported encoding | Use 16-bit PCM WAV under the default 10 MiB cap; MP3 and float WAV are not supported. |
| No microphone / PortAudio error | No input, missing runtime, permissions, or wrong device | Install `libportaudio2`, check host input permissions/settings, inspect devices with `service health`, and select an actual `audio_device`. WAV/text remain usable without hardware. |
| Unknown option or command from older docs | CLI renamed or global option misplaced | Use `config setting set --name ... --value ...`; place `--json`/`--api-url` after `listen`, before the group. Consult `tree --ascii` and per-command help. |
| Edit YAML but service stays unchanged | YAML only seeds an empty database | Use catalog/configuration CLI commands; restarting does not overwrite existing catalogs from YAML. |
| HTTP 401 on normal commands | Server token and CLI environment differ | Supply the same `OPAL_LISTENER_API_TOKEN` to both. Never put credentials in snapshots or committed examples. |
| HTTP 401 on `service start/stop/log` despite a matching token | Lifecycle CLI request helper does not attach the bearer token | This is an implementation limitation. Use the foreground executable and Ctrl-C for token-protected operation; do not disable authentication to expose the service. |
| HTTP 412 while editing | Concurrent configuration change | Retry after reviewing the current value; the CLI fetches fresh revision preconditions automatically. |
| A setting reports `pending_restart` | Desired value differs from startup value | Restart the service, then recheck its metadata. |
| Reaction file appears unrelated to the remote service | Reaction management is local-file based | Manage it on the service machine using matching config/data directories, not merely a remote API URL. |

## Validation scope

The source install, text/catalog/profile/snapshot workflows, positive
and negative Vosk WAV tests, and managed/foreground service lifecycles were
exercised on Linux with Python 3.12. Microphone capture was not opened on the
shared validation host; faster-whisper installation/inference was not run.
Those commands were checked against CLI help and source. OS package installation
and a fresh Git clone were not rerun.

## Next steps

- [Project README](../README.md): project overview and broader capabilities.
- [CLI reference](CLI.md): full command descriptions; check live help for drift.
- [Configuration reference](USER_GUIDE.md#4-configuration-reference) and
  [seed files](../opal_listener/config/): settings, phrase syntax and profiles.
- [Engine/provider architecture](USER_GUIDE.md#32-components) and
  [speech settings](../opal_listener/config/stt.yaml).
- [Cookbook](COOKBOOK.md): more standalone and cross-module recipes.
- [Architecture](USER_GUIDE.md#3-architecture) and
  [OpenAPI contract](../openapi/opal_listen_v0.2.0.yaml), including the
  [shared OPAL kernel](../openapi/shared/opal_kernel.yaml).
- [Docker deployment notes](USER_GUIDE.md#29-docker),
  [Dockerfile](../Dockerfile) and
  [Compose configuration](../opal_listener/docker-compose.yml): optional container
  deployment, model mounts and Linux `/dev/snd` access.

**Known reference drift:** older documentation/scripts still contain removed
commands, and some
pages say authentication does not exist. The implementation has optional bearer
authentication. Contract prose also mixes suite-version labels and retains a
retired settings-update description. The shared kernel defines engine-family
conventions, but this module does not expose a CLI engine inventory. Use this
guide and the installed help for these workflows; do not assume every historical
example or generic shared-contract capability is implemented.
