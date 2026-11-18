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
