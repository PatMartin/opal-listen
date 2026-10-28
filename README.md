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

## Automated verification

GitHub Actions (`/.github/workflows/ci.yml`) reproduces the checks below on
Python 3.11–3.13 using the locked dependency set (`uv.lock`, Linux amd64;
the same commands work on Raspberry Pi arm64 where wheels build natively).

Local equivalents:

```bash
uv sync --locked --extra dev        # reproducible install from uv.lock
uv run ruff check .                 # lint
uv run pytest -m "not vosk" -q      # unit/API/contract/fake-provider tests (no hardware)
uv run pytest -m vosk -q            # optional: needs OPAL_LISTENER_VOSK_MODEL_PATH
uv run pytest opal_listener/tests/test_zmq_events.py -q   # needs sibling ../opal-event checkout
```

Model-dependent tests require a Vosk model and are excluded from the fast CI
job via the `vosk` marker; they run in the optional `model` job or locally with
`OPAL_LISTENER_VOSK_MODEL_PATH` set. The `opal-event` job validates published
ZeroMQ messages against the real OPAL Event protocol and requires a sibling
`../opal-event` checkout.
