# Task

Build an MVP for local-first wakeword, speech-to-text, intent recognition, and slot extraction system for Raspberry Pi 5 and Linux.
Ask clarifying questions as needed.

# Primary goal:

- Implement the contracts specified in opal_listen.yaml.
- Create a nested CLI using Typer for calling the contracts which have been specified in opal_listen.yaml.  Use that as a guide.
- Create a configurable voice recognition engine with a powerful web GUI and matching CLI that can detect wakewords, transcribe short commands, recognize intents, extract slots, test recognition, review failures, and tune performance with strong automated testing and reliability guarantees.
- CLI, GUI, RESTful contracts and Pydantic contracts should be 100% consistent.
- For implementation details, define a ListenerProvider abstraction for supporting pluggable implementations.  We will use VOSK in our MVP but would like the ability to plugin in other providers.

# Tech stack:
- Python 3.11 or 3.12
- FastAPI backend
- React + TypeScript + Vite frontend
- Tailwind CSS
- shadcn/ui
- SQLite
- SQLModel or SQLAlchemy
- Alembic migrations
- Pydantic
- Typer CLI
- Vosk for wakeword detection and fast STT
- Optional Whisper / faster-whisper provider for refined STT
- Docker Compose

# Naming convention:
- Use snake_case everywhere:
  - Python
  - JSON
  - API payloads
  - database columns
  - CLI commands
  - frontend data models

Core service:
Create a 'listener_service` which implements the opal_Listen.yaml contract using a ListenerProvider abstraction which is implemented in VoskListenerProvider using VOSK.  Providers are pluggable so the listener_service should deal only with the abstraction.

It owns:
- microphone/audio capture
- Vosk wakeword detection
- Vosk short command transcription
- optional Whisper refinement
- simple voice activity detection (VAD)
- intent recognition
- slot extraction
- slot normalization
- confidence threshold checks
- cooldown/dedup logic
- recognition logging
- performance tracking

Runtime flow:
1. Microphone audio enters `voice_core_service`
2. Vosk continuously listens for configured wakewords
3. When a wakeword is detected, open a short command session
4. Use VAD to determine speech start/end
5. Transcribe command with Vosk
6. Optionally refine with Whisper
7. Normalize transcript
8. Match intent
9. Extract slots
10. Normalize slot values
11. Apply confidence thresholds
12. Store recognition result and performance metrics
13. Display result in GUI and CLI

V1 success example:
User says:
“computer, turn on the kitchen light”

System outputs:
{
  "wakeword": "computer",
  "transcript": "turn on the kitchen light",
  "normalized_transcript": "turn on the kitchen light",
  "intent_name": "light_turn_on",
  "slot_values": {
    "room_name": "kitchen"
  },
  "wakeword_confidence": 0.88,
  "intent_confidence": 0.93,
  "matched": true,
  "total_latency_ms": 150
}

Required CLI:
Create a nested typer `opal listen` CLI with matching GUI which have parity with the RESTful services.

CLI commands:
- opal listen wakewords list/add/remove
- opal listen intents list/add/remove
- opal listen intents add-phrase
- opal listen test text
- opal listen slots list/add
- opal listen slots add-value
- opal listen slots add-alias
- opal listen test audio
- opal listen test listen
- opal listen test listen-debug
- opal listen test regression
- opal listen failed list/promote
- opal listen perf last/summary
- opal listen profiles list/add
- opal listen config validate
- opal listen system doctor
- opal listen config backup
- opal listen config restore
- opal listen config export/import

Testing requirements:

1. Golden test set
- Known phrases/audio that must always pass

2. Negative test set
- Phrases that must result in no_match

3. Noise test set
- Same phrases with background noise

4. Wakeword tests
- false positive tests
- false negative tests

5. Audio fixture tests
- .wav files processed through full pipeline

6. Regression tests
- Saved failures re-tested automatically

7. Schema validation tests
- Validate all YAML configs

8. API tests
- FastAPI endpoint tests

9. CLI tests
- Command coverage

10. Database migration tests

11. Frontend tests
- Component tests
- End-to-end flows

Reliability requirements:

- Config validation before applying changes
- Config rollback on failure
- Backup and restore support
- Startup health checks:
  - SQLite availability
  - Vosk model path exists
  - microphone available
  - intents compile
  - slots compile
- Watchdog/recovery for audio capture
- Graceful handling of no_match cases

Performance tracking:

Track:
- wakeword_latency_ms
- stt_latency_ms
- intent_latency_ms
- total_latency_ms
- wakeword_confidence
- intent_confidence
- matched / no_match
- audio_level (optional)

Provide:
- last recognition performance
- average latency
- average confidence
- match rate
- recent recognition logs

Do NOT add:
- Prometheus
- Grafana
- distributed tracing
- external monitoring systems

Intent and slot system:

Support:
- training phrases
- slot placeholders
- slot value lists
- slot aliases
- slot normalization

Profiles:

Support profiles such as:
- default
- noisy_room
- car
- kitchen

Profiles can configure:
- wakewords
- model paths
- confidence thresholds
- VAD settings
- enabled intents
- enabled slots

Database:

Use SQLite only.

Tables:
- wakewords
- intents
- intent_phrases
- slots
- slot_values
- slot_aliases
- profiles
- settings
- recognition_logs
- failed_utterances
- regression_tests
- performance_results

Configuration:

Support SQLite + YAML import/export:
- wakewords.yaml
- stt.yaml
- intents.yaml
- slots.yaml
- profiles.yaml
- settings.yaml

Repository structure:

opal_listener/
  service/
  view/
  cli/
  config/
  data/
  models/
  tests/
    audio/
    regression/
    noise/
    negative/
  docker-compose.yml

Deliverables:
1. Architecture diagram
2. Service responsibilities
3. SQLite schema
4. API contract
5. React UI structure
6. CLI design
7. Intent/slot schema
8. Testing strategy
9. Reliability strategy
10. Docker Compose layout
11. Step-by-step implementation which includes verification and testing milestones.

Keep V1 focused.
Goal: build a reliable, testable, tunable wakeword + intent + slot recognition engine before adding execution, TTS, agents, or automation.
