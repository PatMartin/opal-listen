# Implementation plan (V1)

Each step ends with a verification milestone. Steps 1–9 are implemented; step 10 (frontend) is the next pass.

| # | Step | Deliverable | Verification milestone | Status |
|---|---|---|---|---|
| 1 | Project skeleton | `pyproject.toml`, package layout, `AppSettings` (env `OPAL_LISTENER_*`), ruff | `ruff check` clean; `python -m opal_listener` serves `/healthz` | done |
| 2 | Data layer | SQLModel tables, Alembic `0001`, SQLite pragmas (FK, WAL) | `test_migrations.py`: upgrade/downgrade, no model drift, FK enforced | done |
| 3 | Contracts | Pydantic schemas mirroring `opal_listen.yaml` + additive typed models | `test_contract.py`: generated OpenAPI ⊇ contract (paths, params, schemas, required) | done |
| 4 | Intent/slot engine | normalizer, template expansion, numbers, matcher, grammar phrases | `test_nlu_and_audio.py`, golden + negative text sets | done |
| 5 | Config system | YAML schemas, `compile_bundle`, seed, import/export, validate, settings registry | `test_config_schema.py`, `test_backup_and_config.py` | done |
| 6 | Provider abstraction + Vosk | `ListenerProvider`, `VoskListenerProvider` (two-stage wakeword, grammar STT), `FakeListenerProvider`, faster-whisper refiner | audio fixtures: golden, negative, wakeword FP/FN | done |
| 7 | Pipeline + live runtime | `RecognitionPipeline`, VAD, mic capture + resampling, watchdog/backoff, cooldown/dedup, SSE | `test_pipeline_runtime.py`; manual `opal listen test listen-debug` on the Pi (48 kHz USB mic) | done |
| 8 | REST API | routers for all contract tags, error mapping, backups/restore with rollback, health | `test_api.py`, `test_backup_and_config.py` | done |
| 9 | CLI | `opal listen …` over REST, parity map, rich/JSON output, exit codes | `test_cli.py` (every command), `test_e2e_server.py` (uvicorn + SSE) | done |
| 10 | Web GUI | React/TS/Vite/Tailwind/shadcn app in `opal_listener/view/web`, types generated from OpenAPI, `web` compose service | Vitest component tests, Playwright flows mirroring `test_cli.py`, parity check against `OPERATION_COMMANDS` | next |
| 11 | Pi hardening | tune VAD/thresholds per profile with real voices, record real-voice fixtures, systemd/compose autostart | `opal listen perf summary` targets: total_latency p95 < 300 ms, match rate on golden real-voice set ≥ 95 %, 0 wakeword FPs in 1 h of TV audio | next |
| 12 | OPAL SUITE STANDARD v1.2 | `opal_listen.yaml` pinned to `opal_listen.1.2.0.yaml`; correlation-header middleware, strong ETag/If-Match on mutable config, atomic settings PATCH (per-key routes removed), cursor pagination for logs/performance history, `/api/v1/health/live`+`/ready` probes, `OpalError`/`OpalHealthProbe`/`OpalCursorPageMetadata` schemas; CLI and web UI updated in lockstep | `test_contract.py` (full suite incl. CLI/UI parity), `test_api.py`, `test_cli.py` | done |
| 13 | ZeroMQ event bus | `opal listen event publish TOPIC MESSAGE` (CLI, bypasses REST); `--xpub`/`--xsub` global flags (env `OPAL_EVENT_XPUB_URL`/`OPAL_EVENT_XSUB_URL`); server-side `ZmqEventPublisher` connects eagerly at startup and publishes every matched recognition under `opal.listen.intent.match` | `test_cli.py::test_event_publish_command`, `test_zmq_events.py`, `test_api.py::test_intent_match_is_published_to_the_event_bus` | done |

## Commands

```bash
ruff check opal_listener && ruff format --check opal_listener
pytest                                  # full suite (~2.5 min on a Pi 5; audio/noise dominate)
pytest -m "not slow and not vosk"       # fast inner loop (no model needed)
alembic revision --autogenerate -m "…"  # after model changes; test_migrations fails on drift
python -m opal_listener.tests.audio.generate_fixtures
cd opal_listener && docker compose up -d --build
```

## Tuning loop

1. `opal listen test listen-debug` — check ambient dBFS and VAD; adjust profile `vad_settings`.
2. `opal listen failed list` — review no_match utterances captured from the mic.
3. `opal listen failed promote <id> --intent <name>` — adds a delexicalized phrase + regression test.
4. `opal listen test regression` — confirm nothing else broke; `opal listen perf summary` for latency/confidence.
5. `opal listen config export --out-dir opal_listener/config` — commit the tuned YAML.
