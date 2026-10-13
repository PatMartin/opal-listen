# Quickstart

Get from a fresh checkout to recognizing "computer, turn on the kitchen light" in a few minutes.

## 1. Prerequisites

- Linux or Raspberry Pi OS (64-bit), Python 3.11–3.13
- PortAudio for microphone capture: `sudo apt install libportaudio2`
- A Vosk model, e.g. `vosk-model-small-en-us-0.15` from https://alphacephei.com/vosk/models
- Optional: `espeak` + `sox` (only to regenerate audio test fixtures)

## 2. Install

```bash
cd /gh/opal/opal-listen
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ",whisper" for the optional faster-whisper refiner
```

This installs two commands: `opal` (CLI) and `opal-listener-server` (API server).

## 3. Configure

Settings come from environment variables (or a `.env` file in the working directory):

| Variable | Default | Purpose |
|---|---|---|
| `OPAL_LISTENER_VOSK_MODEL_PATH` | `/nabu/ears/vosk/models/vosk-model-small-en-us-0.15` | Vosk model directory |
| `OPAL_LISTENER_DATA_DIR` | `var` | SQLite database and `backups/` |
| `OPAL_LISTENER_CONFIG_DIR` | `opal_listener/config` | YAML used to seed/import configuration |
| `OPAL_LISTENER_HOST` | `0.0.0.0` | Bind address (all interfaces; use `127.0.0.1` for local-only) |
| `OPAL_LISTENER_PORT` | `8000` | API port |
| `OPAL_LISTENER_CORS_ORIGINS` | `["*"]` | Browser origins allowed to call the API (JSON list) |
| `OPAL_LISTEN_API_URL` | `http://localhost:8000` | API URL used by the CLI |
| `OPAL_EVENT_XPUB_URL` | `tcp://127.0.0.1:5559` | ZeroMQ event bus target: the server publishes `opal.listen.intent.match` here on every matched recognition, and `opal listen event publish` connects here too |
| `OPAL_EVENT_XSUB_URL` | `tcp://127.0.0.1:5560` | ZeroMQ event bus subscriber endpoint (CLI-only, reserved for future use) |

```bash
export OPAL_LISTENER_VOSK_MODEL_PATH=/path/to/vosk-model-small-en-us-0.15
```

## 4. Start the server

```bash
opal-listener-server               # or: python -m opal_listener
```

On first start the database is migrated and seeded from `opal_listener/config/*.yaml`
(wakeword `computer`, light/temperature/timer/time/cancel intents, profiles `default`, `noisy_room`, `car`, `kitchen`).
Interactive API docs: http://localhost:8000/docs
Web GUI (single file, same capabilities as the CLI): http://localhost:8000/ui/opal-listen.html

### Access from other machines on the subnet

The server listens on all interfaces by default. Find the Pi's address with `hostname -I`, then from another machine:

```bash
# browser: http://<pi-ip>:8000/ui/opal-listen.html  and  http://<pi-ip>:8000/docs
export OPAL_LISTEN_API_URL=http://<pi-ip>:8000
opal listen system doctor
```

If a firewall is active, open the port for your subnet only, e.g. `sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp`.
The API has no authentication: keep it on a trusted network, or set `OPAL_LISTENER_HOST=127.0.0.1` to restrict it to the Pi.

## 5. Check health

```bash
opal listen system doctor
```

Every line should be `✓`. Microphone problems are warnings (text and audio-file tests still work);
a missing model is an error.

## 6. Try it

```bash
# Text through normalization, intent matching and slot extraction
opal listen test text "computer, turn on the kitchen light"

# A WAV file through the full wakeword -> STT -> intent pipeline
opal listen test audio opal_listener/tests/audio/fixtures/golden_kitchen_light_on.wav

# Live microphone: say "computer ... turn on the kitchen light"
opal listen test listen
opal listen test listen-debug      # also shows audio levels, partial transcripts, wakeword and VAD events
```

Expected result:

```json
{
  "wakeword": "computer",
  "transcript": "turn on the kitchen light",
  "normalized_transcript": "turn on the kitchen light",
  "intent_name": "light_turn_on",
  "slot_values": {"room_name": "kitchen"},
  "wakeword_confidence": 1.0,
  "intent_confidence": 1.0,
  "matched": true,
  "total_latency_ms": 37
}
```

Add `--json` before the group for raw API output, e.g. `opal listen --json intents list`.

## 7. Teach it something new

```bash
# New intent with a slot
opal listen slots add color --value red --value blue
opal listen slots add-alias color red crimson
opal listen intents add light_color --phrase "make the [kitchen] lights {color}"
opal listen test text "make the lights crimson"          # -> slot_values: {"color": "red"}

# Or add phrases to an existing intent
opal listen intents add-phrase light_turn_on "illuminate the {room_name}"
```

Phrase syntax: `{slot}` placeholder, `[optional words]`, `(alternative | choices)`.
Invalid changes (unknown slot, bad syntax, conflicting alias) are rejected with a 422 and nothing is saved.

## 8. Review failures and tune

```bash
opal listen failed list                                  # no_match utterances captured from mic/audio
opal listen failed promote 3 --intent light_turn_on      # adds a phrase + regression test
opal listen test regression                              # exit code 1 if anything broke
opal listen perf summary                                 # latency, confidence, match rate
opal listen profiles activate noisy_room                 # switch thresholds/VAD for the environment
opal listen settings set cooldown_ms 1000
```

## 9. Back up and move configuration

```bash
opal listen config backup
opal listen config backups
opal listen config restore <filename> --yes              # automatic rollback on failure
opal listen config export --out-dir ./my-config          # six YAML files
opal listen config validate --from-dir ./my-config
opal listen config import --from-dir ./my-config --yes   # pre-import backup is taken automatically
```

## 10. Run with Docker

```bash
cd opal_listener
VOSK_MODELS_DIR=/path/to/models docker compose up -d --build
opal listen system doctor
```

The container reads `opal_listener/config` as its seed, mounts models read-only at `/models`, stores
SQLite in the `listener_data` volume and gets `/dev/snd` for the microphone. Set `AUDIO_GID` if the host
`audio` group is not 29, and `OPAL_LISTENER_EXTRAS=whisper` to build with faster-whisper.

## 11. Run the tests

```bash
pytest                               # full suite (Vosk tests skip if the model is missing)
pytest -m "not slow and not vosk"    # fast loop without the model
python scripts/cli_rest_test.py      # every REST operation through the real CLI; prints PASS/FAIL per step
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cannot reach the opal listener API` | Start the server, or set `OPAL_LISTEN_API_URL` / `--api-url` |
| `vosk_model` check fails | Point `OPAL_LISTENER_VOSK_MODEL_PATH` (or a profile's `model_paths.vosk_model`) at a directory containing `am/` and `conf/` |
| Wakeword never triggers | Run `test listen-debug`; if levels stay near -90 dBFS pick another device with `opal listen system devices` and `opal listen settings set audio_device <index or name>` |
| Triggers on similar words | Add them to `opal listen settings set wakeword_distractors "commuter,compute,..."` or raise the wakeword threshold |
| Command cut off or ends too late | Tune the active profile's `vad_settings`, e.g. `opal listen profiles update default --vad silence_end_ms=900` |
| `vocabulary` warning in doctor | A phrase or alias uses a word the model does not know; rephrase it or use a larger model |

More detail: [design.md](design.md) and [implementation_plan.md](implementation_plan.md).
