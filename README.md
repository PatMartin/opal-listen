# opal_listener

Local-first wakeword, speech-to-text, intent recognition and slot extraction for Raspberry Pi 5 and Linux.
FastAPI + SQLite + Vosk, with a Typer CLI (`opal listen ...`) that talks to the REST API. The server also
publishes every matched intent onto a ZeroMQ event bus (topic `opal.listen.intent.match`), and the CLI's
`event publish` can write to that same bus directly.

```
"computer, turn on the kitchen light"
-> {"wakeword": "computer", "intent_name": "light_turn_on", "slot_values": {"room_name": "kitchen"}, ...}
```

## Quick start (local)

```bash
pip install -e ".[dev]"                     # Python 3.11-3.13; add ",whisper" for faster-whisper
export OPAL_LISTENER_VOSK_MODEL_PATH=/path/to/vosk-model-small-en-us-0.15
python -m opal_listener                     # API on http://<host-ip>:8000, all interfaces (docs at /docs)

opal listen system doctor                   # health checks
opal listen test text "computer, turn on the kitchen light"
opal listen test audio opal_listener/tests/audio/fixtures/golden_kitchen_light_on.wav
opal listen test listen-debug               # live microphone with levels, partials and VAD events
```

The first start migrates SQLite (`var/opal_listener.db`) and seeds it from `opal_listener/config/*.yaml`.

## Docker

```bash
cd opal_listener
VOSK_MODELS_DIR=/path/to/models docker compose up -d --build
```

## Tests

```bash
pytest                       # everything (Vosk tests skip if the model is missing)
pytest -m "not slow"         # skip noise robustness tests
python -m opal_listener.tests.audio.generate_fixtures   # regenerate WAV fixtures (espeak + sox)
```

## Documentation

- [docs/QUICKSTART.md](docs/QUICKSTART.md): install, run, test, tune, back up, Docker, troubleshooting
- [docs/design.md](docs/design.md): architecture, services, schema, API, CLI, intent/slot schema, testing, reliability, Docker
- [docs/implementation_plan.md](docs/implementation_plan.md): step-by-step plan with verification milestones
- [opal_listen.yaml](opal_listen.yaml): REST contract, currently pinned to the OPAL SUITE STANDARD v1.2 (`opal_listen.1.2.0.yaml`; `opal_listen.1.0.0.yaml` kept for reference). The generated OpenAPI is tested to be a compatible superset.
