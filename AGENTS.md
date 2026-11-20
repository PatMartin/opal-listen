# Working notes for agents

## Test suite

```bash
python -m pytest opal_listener/tests -q
```

On a checkout where `.env` exists at the repo root, CLI tests load it into `os.environ`
(`opal_listener/dotenv.py` via the Typer entry point) and pollute later tests. Isolate the
suite with:

```bash
OPAL_NO_DOTENV=1 python -m pytest opal_listener/tests -q
```

`@pytest.mark.vosk` tests skip unless the configured Vosk model directory exists. The bundled
model lives at `resources/vosk/models/vosk-model-small-en-us-0.15`; point the suite at it with
`OPAL_LISTENER_VOSK_MODEL_PATH=<abs path>` (the default in `app_settings.py` is a Linux path).

Coverage (`pytest-cov` is a dev extra; `pyproject.toml` sets `source`/`omit`/`skip_covered`):

```bash
OPAL_NO_DOTENV=1 python -m pytest opal_listener/tests -q --cov=opal_listener --cov-report=term-missing
```

## Dependency state

- Microphone: `sounddevice`/PortAudio usable where devices exist; tests instead inject
  `service._source_factory` with `ScriptedSource` (see `tests/test_pipeline_runtime.py`).
- Vosk: library required; model path above. `vosk`-marked tests skip without it.
- faster-whisper: optional (`pip install -e ".[whisper]"`); refinement tests use
  `service._refine_worker._refiner_for` test doubles instead.
- OPAL Event: `pyzmq` and the HTTP publisher are always importable; event delivery is exercised
  via `RecordingEventPublisher` in `conftest.py` — no live opal-event needed for tests.

## Contract

`openapi/opal_listen_v*.yaml` is the upstream API contract; `test_contract.py` asserts the
generated OpenAPI is a compatible superset. Additive optional fields are compatible; changed
fields or new required fields are contract amendments.

## Live smoke test

`scripts/event_say_smoke.py` runs a deterministic end-to-end check: real opal-event +
opal-say subprocesses, a WAV fixture fed through the live capture path (no mic needed), and
assertions on the matched intent, stored event ID, action execution receipt, and nonempty
synthesized WAV. Requires `../opal-event` and `../opal-say` checkouts with working `.venv`s
(override via `OPAL_EVENT_DIR` / `OPAL_SAY_DIR`):

```bash
.venv/Scripts/python.exe scripts/event_say_smoke.py
```

Note: opal-event's ingest route is `POST /api/v1/event/` (singular) — see
`service/http_events.py`.

## Coverage

Branch-inclusive coverage is measured with:

```bash
OPAL_NO_DOTENV=1 python -m pytest opal_listener/tests --cov=opal_listener --cov-branch \
  --cov-report=term-missing --cov-report=html -q
```

CI uploads an `htmlcov` artifact per Python version (visibility only — no `fail_under` yet).

Baseline (v0.2.0, ~550 tests): **~84% statements+branches overall**.
By area: API handlers ~95%, config mgmt ~95%, event publishing ~88%, recognition
pipeline ~88%, CLI ~80%, lifecycle/capture ~55%, providers ~50%
(`vosk_provider`/`whisper_refiner` need the model/extra dep — largely untestable
without them; `dotenv.py` is excluded by design when `OPAL_NO_DOTENV=1`).

Recommended minimums once thresholds are enabled: **80% overall**, with
`view/api/*` and `service/` business logic ≥85%, allowing environment-dependent
files (`capture.py`, `vosk_provider.py`, `whisper_refiner.py`, `dotenv.py`,
`lifecycle.py` process-spawn paths) to sit lower until integration fixtures exist.
