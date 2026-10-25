#!/usr/bin/env python3
"""End-to-end check that every opal_listener REST operation works through the `opal listen` CLI.

WHAT IT DOES
    Runs the real CLI as a subprocess (the CLI always talks HTTP) through an ordered scenario that
    calls every REST operation at least once, verifies each response, and prints one PASS / FAIL /
    SKIP line per step, followed by a summary and a REST coverage check.

        [PASS] 11 wakewords add            create_wakeword  POST /api/v1/wakewords/        0.41s
               created 'hey smoke qzkx' (id 2)
        [FAIL] 26 intents add-phrase       add_phrase       POST /api/v1/intents/{...}     0.38s
               expected exit code 0, got 1: HTTP 422: ...
               $ python -m opal_listener.cli listen --api-url ... --json intents add-phrase ...

    Exit code: 0 if no step failed, 1 otherwise (SKIPs do not fail the run).

TARGET SERVER
    Default: an isolated server is started for the run (`python -m opal_listener` on a free port with
    a temporary data directory), so your real database is never touched. It is stopped at the end.

    --api-url URL: run against an already running server instead. The scenario is designed to leave
    it as it found it: it takes a backup first, uses uniquely named `smoke_<tag>` resources, and at the
    end restores that backup and deletes the backups it created. Note that the restore also discards
    recognition logs written *during* the run. The live pipeline is only started/stopped if it was not
    already running.

COVERAGE
    Each step declares the REST operations it exercises (FastAPI function names, e.g. `create_intent`).
    After the run the server's /openapi.json is read and every operation is checked: operations with no
    step FAIL the coverage check; operations whose steps were all skipped are reported as skipped.

WHAT IS VERIFIED (per group)
    system      ping (/healthz), info (/), doctor (/health/), devices (/health/audio_devices),
                live (/health/live), ready (/health/ready)
    backups     list + create a safety backup before anything is changed
    settings    list, set via PATCH (valid value, rejected invalid value and unknown key, restored)
    wakewords   add, list, get, update (threshold/disabled)
    slots       add with value, add-value with alias, add-alias, list, get, values, aliases, update
    intents     add with phrase, list, get, add-phrase (+ invalid phrase rejected), phrases, update,
                text recognition with the new slot alias, remove-phrase
    profiles    list, add (thresholds/VAD/filters), get, update, activate (recognition uses it),
                re-activate the original profile, remove
    recognition test audio (WAV through wakeword + STT + intent; skipped without a Vosk model),
                pipeline status/start/stop, SSE stream via `test listen` and `test listen-debug`
    logs/perf   logs list/get/count, perf last/summary/history
    failures    add, list, get, update, promote (phrase is delexicalized and then matches), remove
    regression  add, list, get, update, run (only this run's tests are judged), remove
    cleanup     remove alias/value/intent/slot/wakeword created above (the DELETE operations)
    config      validate (server), export to YAML, validate --from-dir, download backup,
                import --from-dir, restore the safety backup (verifies the state), delete run backups

USAGE
    python scripts/cli_rest_test.py                       # isolated server, all steps
    python scripts/cli_rest_test.py --verbose             # also print every command's output
    python scripts/cli_rest_test.py --list                # show the steps and operations, run nothing
    python scripts/cli_rest_test.py --api-url http://pi:8000 --report result.json
    python scripts/cli_rest_test.py --cli opal            # use the installed `opal` entry point
    python scripts/cli_rest_test.py --vosk-model /path/to/vosk-model-small-en-us-0.15

REQUIREMENTS
    Run from anywhere; the repository root is put on PYTHONPATH for the CLI and the isolated server.
    The Python interpreter must have the project dependencies installed. The audio step needs a Vosk
    model (OPAL_LISTENER_VOSK_MODEL_PATH or --vosk-model) and the bundled WAV fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAV = REPO_ROOT / "opal_listener" / "tests" / "audio" / "fixtures" / "golden_kitchen_light_on.wav"
BOX_CHARS = re.compile(r"[│╭╮╰╯─┃━┏┓┗┛]")


# --- result model --------------------------------------------------------------------------------


class CheckFailed(Exception):
    """A verification inside a step did not hold."""


class Skip(Exception):
    """The step cannot run in this environment (e.g. no Vosk model)."""


@dataclass
class CliCall:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def flat(self) -> str:
        """stdout + stderr with rich box characters and line wrapping removed (for substring checks)."""
        return " ".join(BOX_CHARS.sub(" ", self.stdout + " " + self.stderr).split())

    def command(self) -> str:
        return "$ " + " ".join(shlex.quote(a) for a in self.argv)


@dataclass
class StepResult:
    number: int
    name: str
    operations: list[str]
    status: str  # PASS | FAIL | SKIP
    detail: str
    seconds: float
    commands: list[str] = field(default_factory=list)


@dataclass
class Step:
    name: str
    operations: tuple[str, ...]
    func: Callable[[CliRestTest], str | None]
    requires: tuple[str, ...] = ()


STEPS: list[Step] = []


def step(name: str, *operations: str, requires: tuple[str, ...] = ()) -> Callable:
    """Register a scenario step. Steps run in definition order; `requires` names earlier steps."""

    def register(func: Callable[[CliRestTest], str | None]) -> Callable[[CliRestTest], str | None]:
        STEPS.append(Step(name, operations, func, requires))
        return func

    return register


# --- runner --------------------------------------------------------------------------------------


class CliRestTest:
    def __init__(self, cli: list[str], api_url: str, isolated: bool, verbose: bool, wav: Path, tmp: Path) -> None:
        self.cli = cli
        self.api_url = api_url.rstrip("/")
        self.isolated = isolated
        self.verbose = verbose
        self.wav = wav
        self.tmp = tmp
        self.tag = "".join(secrets.choice(string.ascii_lowercase) for _ in range(4))
        self.results: list[StepResult] = []
        self.calls: list[CliCall] = []
        self.state: dict[str, Any] = {}  # values shared between steps (ids, names, filenames)
        self.color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self.env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")])),
            "OPAL_LISTEN_API_URL": self.api_url,
            "COLUMNS": "200",
            "NO_COLOR": "1",
        }

    # names of resources created by this run (unique per run so a shared server is not disturbed)
    @property
    def wakeword(self) -> str:
        return f"hey smoke {self.tag}"

    @property
    def slot(self) -> str:
        return f"smoke_{self.tag}_color"

    @property
    def intent(self) -> str:
        return f"smoke_{self.tag}_paint"

    @property
    def profile(self) -> str:
        return f"smoke_{self.tag}_profile"

    # -- CLI helpers --------------------------------------------------------------------------

    def run(self, *args: str, json_output: bool = True, stdin: str | None = None, timeout: float = 60) -> CliCall:
        """Run `opal listen [--json] <args>` and record the call."""
        argv = [*self.cli, "listen", "--api-url", self.api_url, *(["--json"] if json_output else []), *args]
        try:
            proc = subprocess.run(
                argv, input=stdin, capture_output=True, text=True, env=self.env, timeout=timeout, cwd=REPO_ROOT
            )
            call = CliCall(argv, proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            call = CliCall(argv, -1, _text(exc.stdout), _text(exc.stderr) + f"\n[timed out after {timeout}s]")
        self.calls.append(call)
        if self.verbose:
            print(f"    {call.command()}\n    exit={call.exit_code}")
            for line in (call.stdout + call.stderr).rstrip().splitlines():
                print(f"    | {line}")
        return call

    def ok(self, *args: str, **kwargs: Any) -> Any:
        """Run a command that must succeed; return its parsed JSON output (None if it printed nothing)."""
        call = self.run(*args, **kwargs)
        if call.exit_code != 0:
            raise CheckFailed(f"expected exit code 0, got {call.exit_code}: {_tail(call.flat)}")
        return parse_json(call) if kwargs.get("json_output", True) else call.stdout

    def rejected(self, *args: str, contains: str) -> None:
        """Run a command that must fail with a message containing `contains`."""
        call = self.run(*args)
        if call.exit_code == 0:
            raise CheckFailed(f"expected a non-zero exit code for: {' '.join(args)}")
        if contains.lower() not in call.flat.lower():
            raise CheckFailed(f"expected error containing {contains!r}, got: {_tail(call.flat)}")

    # -- execution ----------------------------------------------------------------------------

    def execute(self, steps: list[Step], operation_routes: dict[str, str]) -> None:
        status_by_name: dict[str, str] = {}
        width = max(len(s.name) for s in steps)
        for number, spec in enumerate(steps, 1):
            first_call = len(self.calls)
            started = time.monotonic()
            failed_deps = [r for r in spec.requires if status_by_name.get(r) != "PASS"]
            try:
                if failed_deps:
                    raise Skip(f"depends on step(s) that did not pass: {', '.join(failed_deps)}")
                detail = spec.func(self) or ""
                status = "PASS"
            except Skip as exc:
                status, detail = "SKIP", str(exc)
            except (CheckFailed, AssertionError) as exc:
                status, detail = "FAIL", str(exc) or "assertion failed"
            except Exception as exc:  # noqa: BLE001 - report unexpected errors as failures, keep going
                status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
            result = StepResult(
                number=number,
                name=spec.name,
                operations=list(spec.operations),
                status=status,
                detail=detail,
                seconds=round(time.monotonic() - started, 2),
                commands=[c.command() for c in self.calls[first_call:]],
            )
            status_by_name[spec.name] = status
            self.results.append(result)
            self.print_result(result, width, operation_routes)

    def print_result(self, result: StepResult, width: int, routes: dict[str, str]) -> None:
        colors = {"PASS": "32", "FAIL": "31", "SKIP": "33"}
        label = f"[{result.status}]"
        if self.color:
            label = f"\033[{colors[result.status]}m{label}\033[0m"
        ops = ", ".join(f"{op} ({routes[op]})" if op in routes else op for op in result.operations)
        print(f"{label} {result.number:02d} {result.name:<{width}}  {ops}  {result.seconds:.2f}s")
        if result.detail:
            print(f"       {result.detail}")
        if result.status == "FAIL" and result.commands:
            print(f"       {result.commands[-1]}")


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _tail(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def parse_json(call: CliCall) -> Any:
    if not call.stdout.strip():
        return None
    try:
        return json.loads(call.stdout)
    except ValueError as exc:
        raise CheckFailed(f"output is not JSON: {_tail(call.stdout)}") from exc


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


# --- scenario ------------------------------------------------------------------------------------
# Steps run top to bottom. Each returns a short description of what it verified.


@step("system ping", "healthz")
def _ping(t: CliRestTest) -> str:
    result = t.ok("system", "ping")
    expect(result == {"status": "ok"}, f"unexpected /healthz response {result}")
    return f"{t.api_url} is up"


@step("system info", "root")
def _info(t: CliRestTest) -> str:
    info = t.ok("system", "info")
    expect(info.get("name") == "opal_listener", f"unexpected / response {info}")
    return f"version {info.get('version')}"


@step("system doctor", "health_check")
def _doctor(t: CliRestTest) -> str:
    call = t.run("system", "doctor")
    report = parse_json(call)
    expect(isinstance(report, dict) and report.get("checks"), "health report has no checks")
    expect(call.exit_code == (1 if report["status"] == "error" else 0), "exit code does not match status")
    checks = {c["name"]: c["status"] for c in report["checks"]}
    t.state["model_ok"] = checks.get("vosk_model") == "ok"
    problems = [f"{n}={s}" for n, s in checks.items() if s != "ok"]
    return f"overall {report['status']}" + (f" ({', '.join(problems)})" if problems else "")


@step("system devices", "get_audio_devices")
def _devices(t: CliRestTest) -> str:
    devices = t.ok("system", "devices")
    expect(isinstance(devices, list), "expected a list of devices")
    return f"{len(devices)} input device(s)"


@step("system live", "get_listen_health_live")
def _live(t: CliRestTest) -> str:
    probe = t.ok("system", "live")
    expect(probe.get("status") == "ok" and probe.get("ready") is True, f"unexpected probe {probe}")
    return f"status {probe['status']}"


@step("system ready", "get_listen_health_ready")
def _ready(t: CliRestTest) -> str:
    call = t.run("system", "ready")
    if call.exit_code == 0:
        probe = parse_json(call)
        expect(
            isinstance(probe, dict) and probe.get("ready") is True and probe.get("status") in ("ok", "degraded"),
            f"unexpected probe {probe}",
        )
        return f"status {probe['status']}"
    # a server that is not ready answers HTTP 503 with the probe as the error body
    expect("503" in call.flat and "ready" in call.flat, f"expected exit 0 or an HTTP 503 probe: {_tail(call.flat)}")
    return "not ready (HTTP 503, probe body returned)"


@step("config backups (before)", "list_backups")
def _backups_before(t: CliRestTest) -> str:
    backups = t.ok("config", "backups")
    t.state["backups_before"] = {b["filename"] for b in backups}
    return f"{len(backups)} existing backup(s)"


@step("config backup", "create_backup")
def _safety_backup(t: CliRestTest) -> str:
    result = t.ok("config", "backup")
    t.state["safety_backup"] = result["filename"]
    return f"safety backup {result['filename']}"


@step("settings list", "get_all_settings")
def _settings_list(t: CliRestTest) -> str:
    settings = t.ok("settings", "list")
    expect("cooldown_ms" in settings, "cooldown_ms missing from settings")
    return f"{len(settings)} settings (cooldown_ms={settings['cooldown_ms']})"


@step("settings set", "patch_listen_settings")
def _settings_set(t: CliRestTest) -> str:
    updated = t.ok("settings", "set", "cooldown_ms", "1234")
    expect(updated["cooldown_ms"] == "1234", f"cooldown_ms not stored: {updated}")
    t.rejected("settings", "set", "cooldown_ms", "soon", contains="cooldown_ms")
    t.rejected("settings", "set", "not_a_setting", "1", contains="not_a_setting")
    restored = t.ok("settings", "set", "cooldown_ms", "1500")
    expect(restored["cooldown_ms"] == "1500", f"cooldown_ms not restored: {restored}")
    return "patched 1234, invalid value + unknown key rejected, restored 1500"


@step("wakewords add", "create_wakeword")
def _ww_add(t: CliRestTest) -> str:
    created = t.ok("wakewords", "add", t.wakeword, "--threshold", "0.75")
    expect(created["name"] == t.wakeword, f"unexpected name {created['name']}")
    return f"created '{created['name']}' (id {created['id']})"


@step("wakewords list", "list_wakewords", requires=("wakewords add",))
def _ww_list(t: CliRestTest) -> str:
    names = [w["name"] for w in t.ok("wakewords", "list")]
    expect(t.wakeword in names, f"{t.wakeword!r} not listed")
    return f"{len(names)} wakeword(s)"


@step("wakewords get", "get_wakeword", requires=("wakewords add",))
def _ww_get(t: CliRestTest) -> str:
    item = t.ok("wakewords", "get", t.wakeword)
    expect(item["confidence_threshold"] == 0.75, f"unexpected threshold {item['confidence_threshold']}")
    return "threshold 0.75"


@step("wakewords update", "update_wakeword", requires=("wakewords add",))
def _ww_update(t: CliRestTest) -> str:
    t.ok("wakewords", "update", t.wakeword, "--threshold", "0.65", "--disabled")
    item = t.ok("wakewords", "get", t.wakeword)
    expect(item["confidence_threshold"] == 0.65 and item["enabled"] is False, f"not updated: {item}")
    return "threshold 0.65, disabled"


@step("slots add", "create_slot", "add_slot_value")
def _slot_add(t: CliRestTest) -> str:
    created = t.ok("slots", "add", t.slot, "--value", "red", "--description", "smoke test colours")
    expect(created["slot_type"] == "list", f"unexpected slot_type {created['slot_type']}")
    return f"created '{t.slot}' with value 'red'"


@step("slots add-value", "add_slot_value", "add_alias", requires=("slots add",))
def _slot_add_value(t: CliRestTest) -> str:
    created = t.ok("slots", "add-value", t.slot, "blue", "--alias", "navy")
    expect(created["value"] == "blue", f"unexpected value {created}")
    return "value 'blue' with alias 'navy'"


@step("slots add-alias", "add_alias", requires=("slots add",))
def _slot_add_alias(t: CliRestTest) -> str:
    created = t.ok("slots", "add-alias", t.slot, "red", "crimson")
    expect(created["alias"] == "crimson", f"unexpected alias {created}")
    return "alias 'crimson' -> 'red'"


@step("slots list", "list_slots", requires=("slots add",))
def _slot_list(t: CliRestTest) -> str:
    names = [s["name"] for s in t.ok("slots", "list")]
    expect(t.slot in names, f"{t.slot!r} not listed")
    return f"{len(names)} slot(s)"


@step("slots get", "get_slot", requires=("slots add-value", "slots add-alias"))
def _slot_get(t: CliRestTest) -> str:
    slot = t.ok("slots", "get", t.slot)
    values = {v["value"]: sorted(a["alias"] for a in v["aliases"]) for v in slot["values"]}
    expect(values == {"red": ["crimson"], "blue": ["navy"]}, f"unexpected values {values}")
    return f"values {values}"


@step("slots values", "list_slot_values", requires=("slots add-value",))
def _slot_values(t: CliRestTest) -> str:
    values = [v["value"] for v in t.ok("slots", "values", t.slot)]
    expect(values == ["red", "blue"], f"unexpected values {values}")
    return "red, blue"


@step("slots aliases", "list_aliases", requires=("slots add-alias",))
def _slot_aliases(t: CliRestTest) -> str:
    aliases = [a["alias"] for a in t.ok("slots", "aliases", t.slot, "red")]
    expect(aliases == ["crimson"], f"unexpected aliases {aliases}")
    return "crimson"


@step("slots update", "update_slot", requires=("slots add",))
def _slot_update(t: CliRestTest) -> str:
    updated = t.ok("slots", "update", t.slot, "--description", "updated by smoke test")
    expect(updated["description"] == "updated by smoke test", f"not updated: {updated}")
    return "description updated"


@step("intents add", "create_intent", requires=("slots add",))
def _intent_add(t: CliRestTest) -> str:
    created = t.ok("intents", "add", t.intent, "--phrase", f"paint the wall {{{t.slot}}}", "--threshold", "0.75")
    expect(len(created["phrases"]) == 1, f"unexpected phrases {created['phrases']}")
    return f"created '{t.intent}' with 1 phrase"


@step("intents list", "list_intents", requires=("intents add",))
def _intent_list(t: CliRestTest) -> str:
    names = [i["name"] for i in t.ok("intents", "list")]
    expect(t.intent in names, f"{t.intent!r} not listed")
    return f"{len(names)} intent(s)"


@step("intents get", "get_intent", requires=("intents add",))
def _intent_get(t: CliRestTest) -> str:
    intent = t.ok("intents", "get", t.intent)
    expect(intent["confidence_threshold"] == 0.75, f"unexpected intent {intent}")
    return f"id {intent['id']}"


@step("intents add-phrase", "add_phrase", requires=("intents add",))
def _intent_add_phrase(t: CliRestTest) -> str:
    created = t.ok("intents", "add-phrase", t.intent, f"color it {{{t.slot}}}")
    expect(created["phrase"] == f"color it {{{t.slot}}}", f"unexpected phrase {created}")
    t.rejected("intents", "add-phrase", t.intent, "paint {ghost_slot}", contains="unknown slot")
    return "valid phrase added, phrase with unknown slot rejected (validate before apply)"


@step("intents phrases", "list_phrases", requires=("intents add-phrase",))
def _intent_phrases(t: CliRestTest) -> str:
    phrases = [p["phrase"] for p in t.ok("intents", "phrases", t.intent)]
    expect(len(phrases) == 2, f"expected 2 phrases, got {phrases}")
    return f"{len(phrases)} phrases"


@step("intents update", "update_intent", requires=("intents add",))
def _intent_update(t: CliRestTest) -> str:
    updated = t.ok("intents", "update", t.intent, "--threshold", "0.8", "--description", "smoke test")
    expect(updated["confidence_threshold"] == 0.8, f"not updated: {updated}")
    return "threshold 0.8"


@step("test text", "test_text", requires=("intents add", "slots add-alias"))
def _test_text(t: CliRestTest) -> str:
    result = t.ok("test", "text", "paint the wall crimson", "--expect-intent", t.intent)
    expect(result["slot_values"] == {t.slot: "red"}, f"unexpected slots {result['slot_values']}")
    return f"'paint the wall crimson' -> {result['intent_name']} {result['slot_values']}"


@step("intents remove-phrase", "delete_phrase", requires=("intents add-phrase",))
def _intent_remove_phrase(t: CliRestTest) -> str:
    t.ok("intents", "remove-phrase", t.intent, f"color it {{{t.slot}}}")
    phrases = [p["phrase"] for p in t.ok("intents", "phrases", t.intent)]
    expect(len(phrases) == 1, f"phrase not removed: {phrases}")
    return "1 phrase left"


@step("profiles list", "list_profiles")
def _profiles_list(t: CliRestTest) -> str:
    profiles = t.ok("profiles", "list")
    active = [p["name"] for p in profiles if p["is_active"]]
    t.state["original_profile"] = active[0] if active else None
    return f"{len(profiles)} profile(s), active: {t.state['original_profile']}"


@step("profiles add", "create_profile", requires=("intents add",))
def _profile_add(t: CliRestTest) -> str:
    created = t.ok(
        "profiles", "add", t.profile,
        "--threshold", "intent=0.75", "--vad", "silence_end_ms=800",
        "--intent", t.intent, "--slot", t.slot,
    )  # fmt: skip
    expect(created["vad_settings"] == {"silence_end_ms": 800}, f"unexpected vad {created['vad_settings']}")
    expect(created["is_active"] is False, "new profile should not be active")
    return f"created '{t.profile}'"


@step("profiles get", "get_profile", requires=("profiles add",))
def _profile_get(t: CliRestTest) -> str:
    profile = t.ok("profiles", "get", t.profile)
    expect(profile["enabled_intents"] == [t.intent], f"unexpected profile {profile}")
    return f"enabled_intents {profile['enabled_intents']}"


@step("profiles update", "update_profile", requires=("profiles add",))
def _profile_update(t: CliRestTest) -> str:
    updated = t.ok("profiles", "update", t.profile, "--description", "smoke profile")
    expect(updated["description"] == "smoke profile", f"not updated: {updated}")
    t.rejected("profiles", "update", t.profile, "--vad", "silence_end_ms=1", contains="silence_end_ms")
    return "description updated, invalid VAD value rejected"


@step("profiles activate", "activate_profile", requires=("profiles add", "test text"))
def _profile_activate(t: CliRestTest) -> str:
    t.ok("profiles", "activate", t.profile)
    result = t.ok("test", "text", "paint the wall navy", "--no-save", "--verbose")
    expect(result["profile_name"] == t.profile, f"recognition used profile {result['profile_name']}")
    expect(result["slot_values"] == {t.slot: "blue"}, f"unexpected slots {result['slot_values']}")
    original = t.state.get("original_profile")
    if original:
        t.ok("profiles", "activate", original)
        active = [p["name"] for p in t.ok("profiles", "list") if p["is_active"]]
        expect(active == [original], f"original profile not re-activated: {active}")
    return f"recognition used '{t.profile}', '{original}' re-activated"


@step("profiles remove", "delete_profile", requires=("profiles add",))
def _profile_remove(t: CliRestTest) -> str:
    t.ok("profiles", "remove", t.profile)
    names = [p["name"] for p in t.ok("profiles", "list")]
    expect(t.profile not in names, "profile still listed")
    return "removed"


@step("test audio", "test_audio")
def _test_audio(t: CliRestTest) -> str:
    if not t.wav.exists():
        raise Skip(f"WAV fixture not found: {t.wav}")
    if not t.state.get("model_ok"):
        raise Skip("server reports no usable Vosk model (see 'system doctor')")
    result = t.ok("test", "audio", str(t.wav), "--require-wakeword", timeout=180)
    expect({"wakeword", "transcript", "intent_name", "total_latency_ms"} <= set(result), f"incomplete {result}")
    if t.isolated and t.wav == DEFAULT_WAV:
        expect(result["intent_name"] == "light_turn_on", f"expected light_turn_on, got {result['intent_name']}")
        expect(result["slot_values"] == {"room_name": "kitchen"}, f"unexpected slots {result['slot_values']}")
    return f"{t.wav.name}: {result['transcript']!r} -> {result['intent_name']} {result['slot_values']}"


@step("pipeline status", "get_status")
def _pipeline_status(t: CliRestTest) -> str:
    status = t.ok("pipeline", "status")
    expect("running" in status and "state" in status, f"incomplete status {status}")
    t.state["pipeline_was_running"] = status["running"]
    return f"state {status['state']}, provider {status['provider']}"


@step("pipeline start", "start_pipeline", requires=("pipeline status",))
def _pipeline_start(t: CliRestTest) -> str:
    result = t.ok("pipeline", "start")
    expected = "already_running" if t.state["pipeline_was_running"] else "started"
    expect(result["status"] == expected, f"expected {expected}, got {result['status']}")
    return result["status"]


@step("pipeline stop", "stop_pipeline", requires=("pipeline start",))
def _pipeline_stop(t: CliRestTest) -> str:
    if t.state["pipeline_was_running"]:
        raise Skip("pipeline was already running before the test; not stopping a live pipeline")
    result = t.ok("pipeline", "stop")
    expect(result["status"] == "stopped", f"expected stopped, got {result['status']}")
    expect(t.ok("pipeline", "status")["running"] is False, "pipeline still running")
    return "stopped"


@step("test listen", "stream_results")
def _test_listen(t: CliRestTest) -> str:
    t.ok("test", "listen", "--no-start", "--duration", "2", timeout=30)
    return "SSE stream opened and closed after the idle timeout"


@step("test listen-debug", "stream_results")
def _test_listen_debug(t: CliRestTest) -> str:
    output = t.ok("test", "listen-debug", "--no-start", "--duration", "2", json_output=False, timeout=30)
    expect("status:" in output, f"no status event received: {_tail(output)}")
    return "received the initial 'status' event"


@step("logs list", "list_logs", requires=("test text",))
def _logs_list(t: CliRestTest) -> str:
    logs = t.ok("logs", "list", "--limit", "5")
    expect(len(logs) >= 1, "no recognition logs")
    t.state["log_id"] = logs[0]["id"]
    return f"latest log id {logs[0]['id']}: {logs[0]['normalized_transcript']!r}"


@step("logs get", "get_log", requires=("logs list",))
def _logs_get(t: CliRestTest) -> str:
    log = t.ok("logs", "get", str(t.state["log_id"]))
    expect(log["id"] == t.state["log_id"], f"unexpected log {log}")
    return f"source {log['source']}, matched {log['matched']}"


@step("logs count", "count_logs", requires=("test text",))
def _logs_count(t: CliRestTest) -> str:
    counts = t.ok("logs", "count")
    expect(counts["total"] >= 1 and counts["total"] == counts["matched"] + counts["no_match"], f"bad {counts}")
    return f"{counts}"


@step("perf last", "get_last_performance", requires=("test text",))
def _perf_last(t: CliRestTest) -> str:
    last = t.ok("perf", "last")
    expect(last and "total_latency_ms" in last, f"no performance result: {last}")
    return f"total_latency_ms {last['total_latency_ms']}"


@step("perf summary", "get_performance_summary", requires=("test text",))
def _perf_summary(t: CliRestTest) -> str:
    summary = t.ok("perf", "summary", "--limit", "50")
    expect(summary["count"] >= 1, f"empty summary {summary}")
    return f"{summary['count']} results, match rate {summary['match_rate']}"


@step("perf history", "get_performance_history", requires=("test text",))
def _perf_history(t: CliRestTest) -> str:
    history = t.ok("perf", "history", "--limit", "5")
    expect(len(history) >= 1, "empty history")
    return f"{len(history)} row(s)"


@step("failed add", "create_failed_utterance")
def _failed_add(t: CliRestTest) -> str:
    created = t.ok("failed", "add", "tint the wall navy", "--notes", "smoke test")
    t.state["failed_id"] = created["id"]
    return f"id {created['id']}"


@step("failed list", "list_failed_utterances", requires=("failed add",))
def _failed_list(t: CliRestTest) -> str:
    ids = [f["id"] for f in t.ok("failed", "list", "--unreviewed", "--limit", "500")]
    expect(t.state["failed_id"] in ids, "failed utterance not listed as unreviewed")
    return f"{len(ids)} unreviewed"


@step("failed get", "get_failed_utterance", requires=("failed add",))
def _failed_get(t: CliRestTest) -> str:
    item = t.ok("failed", "get", str(t.state["failed_id"]))
    expect(item["notes"] == "smoke test", f"unexpected {item}")
    return "notes match"


@step("failed update", "update_failed_utterance", requires=("failed add",))
def _failed_update(t: CliRestTest) -> str:
    updated = t.ok("failed", "update", str(t.state["failed_id"]), "--reviewed", "--expected-intent", t.intent)
    expect(updated["reviewed"] is True and updated["expected_intent"] == t.intent, f"not updated: {updated}")
    return "marked reviewed"


@step("failed promote", "promote_to_phrase", requires=("failed add", "intents add"))
def _failed_promote(t: CliRestTest) -> str:
    result = t.ok("failed", "promote", str(t.state["failed_id"]), "--intent", t.intent)
    phrase = result["phrase"]["phrase"]
    expect(phrase == f"tint the wall {{{t.slot}}}", f"unexpected promoted phrase {phrase!r}")
    expect(result["failed_utterance"]["promoted_to_phrase"] is True, "not flagged as promoted")
    t.state["promoted_regression_id"] = (result.get("regression_test") or {}).get("id")
    matched = t.ok("test", "text", "tint the wall red", "--no-save")
    expect(matched["intent_name"] == t.intent, f"promoted phrase does not match: {matched['intent_name']}")
    return f"phrase {phrase!r} now matches 'tint the wall red'"


@step("failed remove", "delete_failed_utterance", requires=("failed add",))
def _failed_remove(t: CliRestTest) -> str:
    t.ok("failed", "remove", str(t.state["failed_id"]))
    t.rejected("failed", "get", str(t.state["failed_id"]), contains="404")
    return "removed (get now returns 404)"


@step("regression add", "create_regression_test", requires=("intents add",))
def _reg_add(t: CliRestTest) -> str:
    created = t.ok(
        "regression", "add", f"smoke_{t.tag}_blue", "paint the wall blue",
        "--expect-intent", t.intent, "--slot", f"{t.slot}=blue",
    )  # fmt: skip
    t.state["regression_id"] = created["id"]
    return f"id {created['id']}"


@step("regression list", "list_regression_tests", requires=("regression add",))
def _reg_list(t: CliRestTest) -> str:
    ids = [r["id"] for r in t.ok("regression", "list")]
    expect(t.state["regression_id"] in ids, "regression test not listed")
    return f"{len(ids)} test(s)"


@step("regression get", "get_regression_test", requires=("regression add",))
def _reg_get(t: CliRestTest) -> str:
    item = t.ok("regression", "get", str(t.state["regression_id"]))
    expect(item["expected_slots"] == {t.slot: "blue"}, f"unexpected {item}")
    return "expected_slots match"


@step("regression update", "update_regression_test", requires=("regression add",))
def _reg_update(t: CliRestTest) -> str:
    updated = t.ok("regression", "update", str(t.state["regression_id"]), "--name", f"smoke_{t.tag}_renamed")
    expect(updated["name"] == f"smoke_{t.tag}_renamed", f"not renamed: {updated}")
    return "renamed"


@step("test regression", "run_regression_tests", requires=("regression add",))
def _reg_run(t: CliRestTest) -> str:
    call = t.run("test", "regression", "--all")
    run = parse_json(call)
    expect(isinstance(run, dict) and "results" in run, f"unexpected output: {_tail(call.flat)}")
    expect(call.exit_code == (1 if run["failed"] else 0), "exit code does not reflect failures")
    mine = {t.state["regression_id"], t.state.get("promoted_regression_id")} - {None}
    judged = [r for r in run["results"] if r["test_id"] in mine]
    expect(len(judged) == len(mine), f"expected {len(mine)} of this run's tests in the results")
    failed = [r for r in judged if not r["passed"]]
    expect(not failed, f"this run's regression tests failed: {failed}")
    other = f", {run['failed']} failure(s) in pre-existing tests" if run["failed"] else ""
    return f"{len(judged)} of this run's tests passed ({run['passed']}/{run['total']} overall{other})"


@step("regression remove", "delete_regression_test", requires=("regression add",))
def _reg_remove(t: CliRestTest) -> str:
    ids = [t.state["regression_id"], t.state.get("promoted_regression_id")]
    for test_id in filter(None, ids):
        t.ok("regression", "remove", str(test_id))
    remaining = {r["id"] for r in t.ok("regression", "list")}
    expect(not remaining & set(filter(None, ids)), "regression tests still listed")
    return f"removed {len(list(filter(None, ids)))} test(s)"


@step("slots remove-alias", "delete_alias", requires=("slots add-alias",))
def _slot_remove_alias(t: CliRestTest) -> str:
    t.ok("slots", "remove-alias", t.slot, "red", "crimson")
    expect(t.ok("slots", "aliases", t.slot, "red") == [], "alias still listed")
    return "crimson removed"


@step("slots remove-value", "delete_slot_value", requires=("slots add-value",))
def _slot_remove_value(t: CliRestTest) -> str:
    t.ok("slots", "remove-value", t.slot, "blue")
    values = [v["value"] for v in t.ok("slots", "values", t.slot)]
    expect(values == ["red"], f"unexpected values {values}")
    return "blue removed"


@step("intents remove", "delete_intent", requires=("intents add",))
def _intent_remove(t: CliRestTest) -> str:
    t.ok("intents", "remove", t.intent)
    names = [i["name"] for i in t.ok("intents", "list")]
    expect(t.intent not in names, "intent still listed")
    return "removed"


@step("slots remove", "delete_slot", requires=("slots add", "intents remove"))
def _slot_remove(t: CliRestTest) -> str:
    t.ok("slots", "remove", t.slot)
    names = [s["name"] for s in t.ok("slots", "list")]
    expect(t.slot not in names, "slot still listed")
    return "removed"


@step("wakewords remove", "delete_wakeword", requires=("wakewords add",))
def _ww_remove(t: CliRestTest) -> str:
    t.ok("wakewords", "remove", t.wakeword)
    names = [w["name"] for w in t.ok("wakewords", "list")]
    expect(t.wakeword not in names, "wakeword still listed")
    return "removed"


@step("config validate", "validate_config")
def _config_validate(t: CliRestTest) -> str:
    call = t.run("config", "validate")
    report = parse_json(call)
    expect(isinstance(report, dict) and "valid" in report, f"unexpected output: {_tail(call.flat)}")
    expect(call.exit_code == (0 if report["valid"] else 1), "exit code does not match 'valid'")
    if t.isolated:
        expect(report["valid"], f"shipped configuration is invalid: {report['errors']}")
    return f"valid={report['valid']} sources={report.get('sources')}"


@step("config export", "export_config")
def _config_export(t: CliRestTest) -> str:
    out_dir = t.tmp / "export"
    t.ok("config", "export", "--out-dir", str(out_dir))
    files = sorted(p.name for p in out_dir.glob("*.yaml"))
    wanted = sorted(["wakewords.yaml", "stt.yaml", "intents.yaml", "slots.yaml", "profiles.yaml", "settings.yaml"])
    expect(files == wanted, f"unexpected export files {files}")
    t.state["export_dir"] = out_dir
    return f"6 YAML files in {out_dir}"


@step("config validate --from-dir", "validate_listen_backup_payload", requires=("config export",))
def _config_validate_dir(t: CliRestTest) -> str:
    report = t.ok("config", "validate", "--from-dir", str(t.state["export_dir"]))
    expect(report["valid"] is True, f"exported configuration does not validate: {report['errors']}")
    return "exported YAML validates"


@step("config download", "download_backup", requires=("config backup",))
def _config_download(t: CliRestTest) -> str:
    t.ok("config", "download", t.state["safety_backup"], "--out", str(t.tmp))
    data = (t.tmp / t.state["safety_backup"]).read_bytes()
    expect(data[:15] == b"SQLite format 3", "downloaded file is not a SQLite database")
    return f"{len(data)} bytes"


@step("config import", "import_config", requires=("config export",))
def _config_import(t: CliRestTest) -> str:
    result = t.ok("config", "import", "--from-dir", str(t.state["export_dir"]), "--yes")
    expect(result["status"] == "imported" and result["backup_filename"], f"unexpected {result}")
    return f"imported {result['counts']} (pre-import backup {result['backup_filename']})"


@step("config restore", "restore_backup", requires=("config backup",))
def _config_restore(t: CliRestTest) -> str:
    result = t.ok("config", "restore", t.state["safety_backup"], "--yes")
    expect(result["status"] == "restored", f"unexpected {result}")
    names = [w["name"] for w in t.ok("wakewords", "list")]
    expect(t.wakeword not in names, "restore did not return to the pre-test state")
    return "database restored to the state before this run"


@step("config delete-backup", "delete_backup", requires=("config backups (before)",))
def _config_delete_backups(t: CliRestTest) -> str:
    created = {b["filename"] for b in t.ok("config", "backups")} - t.state["backups_before"]
    for filename in sorted(created):
        t.ok("config", "delete-backup", filename, "--yes")
    after = {b["filename"] for b in t.ok("config", "backups")}
    expect(after == t.state["backups_before"], f"leftover backups: {sorted(after - t.state['backups_before'])}")
    return f"deleted {len(created)} backup(s) created by this run"


# --- server + coverage ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_get_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local test server URL
        return json.loads(response.read())


def start_isolated_server(tmp: Path, vosk_model: str | None) -> tuple[subprocess.Popen[bytes], str, Path]:
    port = free_port()
    log_path = tmp / "server.log"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")])),
        "OPAL_LISTENER_DATA_DIR": str(tmp / "data"),
        "OPAL_LISTENER_HOST": "127.0.0.1",
        "OPAL_LISTENER_PORT": str(port),
    }
    if vosk_model:
        env["OPAL_LISTENER_VOSK_MODEL_PATH"] = vosk_model
    log = log_path.open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "opal_listener"], cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited with code {proc.returncode}; see {log_path}")
        try:
            if http_get_json(f"{url}/healthz", timeout=1).get("status") == "ok":
                return proc, url, log_path
        except OSError:
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError(f"server did not become healthy within 60s; see {log_path}")


def stop_server(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def operation_routes(api_url: str) -> dict[str, str]:
    """Map FastAPI function name -> 'METHOD /path' using the server's OpenAPI document.

    FastAPI operationIds are f"{name}{path}" with non-word characters replaced by '_', plus f"_{method}".
    """
    spec = http_get_json(f"{api_url}/openapi.json")
    routes: dict[str, str] = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            suffix = re.sub(r"\W", "_", path) + f"_{method}"
            op_id = operation["operationId"]
            name = op_id[: -len(suffix)] if op_id.endswith(suffix) else op_id
            routes[name] = f"{method.upper()} {path}"
    return routes


def coverage_result(results: list[StepResult], routes: dict[str, str]) -> StepResult:
    attempted = {op for r in results if r.status != "SKIP" for op in r.operations}
    skipped = {op for r in results if r.status == "SKIP" for op in r.operations} - attempted
    missing = sorted(set(routes) - attempted - skipped)
    detail = f"{len(set(routes) & attempted)}/{len(routes)} REST operations exercised"
    if skipped:
        detail += f"; skipped: {', '.join(sorted(skipped))}"
    if missing:
        detail += f"; NOT covered by any step: {', '.join(missing)}"
    return StepResult(len(results) + 1, "REST coverage", [], "FAIL" if missing else "PASS", detail, 0.0)


# --- main ----------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise every opal_listener REST operation through the `opal listen` CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See the module docstring (head of this file) for the full description of every step.",
    )
    parser.add_argument("--api-url", help="use this running server instead of starting an isolated one")
    parser.add_argument(
        "--cli",
        default=f"{shlex.quote(sys.executable)} -m opal_listener.cli",
        help="command that runs the CLI (default: this interpreter with -m opal_listener.cli)",
    )
    parser.add_argument("--wav", type=Path, default=DEFAULT_WAV, help="WAV file for the 'test audio' step")
    parser.add_argument("--vosk-model", help="Vosk model directory for the isolated server")
    parser.add_argument("--report", type=Path, help="also write results as JSON to this file")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory (server log, exports)")
    parser.add_argument("--verbose", "-v", action="store_true", help="print every CLI command and its output")
    parser.add_argument("--list", action="store_true", help="list the steps and their REST operations, then exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        width = max(len(s.name) for s in STEPS)
        for number, spec in enumerate(STEPS, 1):
            print(f"{number:02d} {spec.name:<{width}}  {', '.join(spec.operations)}")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="opal_cli_rest_test_"))
    server: subprocess.Popen[bytes] | None = None
    started = time.monotonic()
    try:
        if args.api_url:
            api_url, isolated = args.api_url, False
            print(f"Target: existing server {api_url} (state is backed up and restored)")
        else:
            server, api_url, log_path = start_isolated_server(tmp, args.vosk_model)
            isolated = True
            print(f"Target: isolated server {api_url} (data in {tmp / 'data'}, log {log_path})")
        try:
            routes = operation_routes(api_url)
        except OSError as exc:
            print(f"Cannot read {api_url}/openapi.json: {exc}")
            return 1
        runner = CliRestTest(shlex.split(args.cli), api_url, isolated, args.verbose, args.wav.resolve(), tmp)
        print(f"CLI: {args.cli}   run tag: {runner.tag}   steps: {len(STEPS)}\n")
        runner.execute(STEPS, routes)

        coverage = coverage_result(runner.results, routes)
        runner.print_result(coverage, max(len(s.name) for s in STEPS), routes)
        results = [*runner.results, coverage]
    finally:
        if server is not None:
            stop_server(server)

    counts = {s: sum(1 for r in results if r.status == s) for s in ("PASS", "FAIL", "SKIP")}
    elapsed = time.monotonic() - started
    print(
        f"\n==== {counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped "
        f"({len(results)} checks) in {elapsed:.1f}s"
    )
    for result in results:
        if result.status != "PASS":
            print(f"  {result.status}  {result.number:02d} {result.name}: {result.detail}")

    if args.report:
        args.report.write_text(
            json.dumps(
                {
                    "api_url": api_url,
                    "isolated": isolated,
                    "passed": counts["PASS"],
                    "failed": counts["FAIL"],
                    "skipped": counts["SKIP"],
                    "seconds": round(elapsed, 1),
                    "results": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
        print(f"report written to {args.report}")
    if args.keep:
        print(f"kept {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
