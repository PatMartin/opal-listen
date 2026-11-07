"""File-provisioned intent reactions: match conditions -> shell commands.

Reactions live in ``config_dir/reactions.yaml`` — deliberately outside the database and
ConfigBundle so the REST API can never write executable commands (API-mutable shell
commands would be a remote-code-execution vector). The file is re-stat'd on every
dispatch, so edits hot-reload without a watcher.

The shape mirrors opal-event's rule/action model (``{path, operator, value}``
conditions, ``stdin: event_json`` delivery) so reactions migrate to opal-event rules
unchanged if the suite is later adopted.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

Operator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "exists", "contains"]


class ReactionCondition(BaseModel):
    """One predicate over the recognition-result payload; opal-event condition grammar."""

    path: str  # dotted, e.g. "intent_name" or "slot_values.room"
    operator: Operator
    value: Any = None


class ReactionAction(BaseModel):
    """A subprocess invocation. argv form only — no shell, so payloads can't inject."""

    type: Literal["command"] = "command"
    argv: list[str] = Field(min_length=1)
    timeout_s: float = Field(default=15.0, ge=1.0, le=3600.0)
    cwd: str | None = None
    env: dict[str, str] | None = None


class Reaction(BaseModel):
    name: str
    enabled: bool = True
    conditions: list[ReactionCondition] = Field(default_factory=list)
    actions: list[ReactionAction] = Field(min_length=1)


class ReactionsFile(BaseModel):
    reactions: list[Reaction] = Field(default_factory=list)


def _resolve_path(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Walk a dotted path ('a.b.c') through dicts/lists. Returns (present, value)."""
    node: Any = payload
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def check_condition(cond: ReactionCondition, payload: dict[str, Any]) -> bool:
    present, left = _resolve_path(payload, cond.path)
    op = cond.operator
    if op == "exists":
        return present
    if not present:
        return False
    right = cond.value
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op in ("gt", "gte", "lt", "lte"):
        try:
            lf, rf = float(left), float(right)
        except (TypeError, ValueError):
            return False
        return {"gt": lf > rf, "gte": lf >= rf, "lt": lf < rf, "lte": lf <= rf}[op]
    if op == "in":
        return left in right if isinstance(right, (list, str)) else False
    if op == "not_in":
        return left not in right if isinstance(right, (list, str)) else False
    if op == "contains":
        return right in left if isinstance(left, (list, str)) else False
    return False


def load_reactions(path: Path) -> list[Reaction]:
    """Parse reactions.yaml; missing file -> empty set, invalid file -> log + empty set."""
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ReactionsFile.model_validate(data).reactions
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        log.warning("reactions file %s is invalid, no reactions will fire: %s", path, exc)
        return []


@dataclass
class _RunnerStats:
    fired: int = 0
    failed: int = 0
    dropped: int = 0
    last_error: str | None = None


class ReactionRunner:
    """Serial worker: dispatch() is non-blocking; matched actions run one at a time.

    A full queue drops the job (recognition must never wait on a shell command);
    every execution appends a JSON line to the reactions log file."""

    def __init__(self, config_path: Path, log_path: Path, capacity: int = 64) -> None:
        self._config_path = config_path
        self._log_path = log_path
        self._queue: queue.Queue[tuple[list[ReactionAction], dict[str, Any], str]] = queue.Queue(capacity)
        self._stats = _RunnerStats()
        self._stat_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loaded_mtime_ns: int | None = None
        self._loaded: list[Reaction] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._worker, name="reaction-runner", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(([], {}, "__stop__"))
        if self._thread is not None:
            self._thread.join(timeout=5)

    def stats(self) -> dict[str, float]:
        with self._stat_lock:
            s = self._stats
            return {
                "reactions_fired": float(s.fired),
                "reactions_failed": float(s.failed),
                "reactions_dropped": float(s.dropped),
                "reaction_queue_depth": float(self._queue.qsize()),
            }

    def _reactions(self) -> list[Reaction]:
        """Reload the file only when it changed on disk (cheap stat per dispatch)."""
        try:
            mtime = self._config_path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._loaded_mtime_ns:
            self._loaded_mtime_ns = mtime
            self._loaded = load_reactions(self._config_path)
        return self._loaded

    def dispatch(self, payload: dict[str, Any]) -> int:
        """Evaluate conditions and enqueue matching actions. Returns matches found.

        Runs on the recognition thread; must stay fast — condition eval only."""
        matched: list[ReactionAction] = []
        names: list[str] = []
        for reaction in self._reactions():
            if not reaction.enabled:
                continue
            if all(check_condition(c, payload) for c in reaction.conditions):
                matched.extend(reaction.actions)
                names.append(reaction.name)
        if not matched:
            return 0
        try:
            self._queue.put_nowait((matched, payload, ",".join(names)))
        except queue.Full:
            with self._stat_lock:
                self._stats.dropped += 1
                self._stats.last_error = f"queue full, dropped reaction(s) {names}"
            log.warning("reaction queue full, dropped: %s", names)
        return len(names)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                actions, payload, names = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if names == "__stop__":
                return
            for action in actions:
                self._execute(action, payload, names)

    def _execute(self, action: ReactionAction, payload: dict[str, Any], names: str) -> None:
        started = time.monotonic()
        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reactions": names,
            "argv": action.argv,
        }
        try:
            completed = subprocess.run(
                action.argv,
                input=json.dumps(payload, default=str),
                capture_output=True,
                text=True,
                timeout=action.timeout_s,
                cwd=action.cwd,
                env={**os.environ, **(action.env or {})},
            )
            record.update(exit_code=completed.returncode, stderr_tail=(completed.stderr or "")[-500:])
            failed = completed.returncode != 0
        except subprocess.TimeoutExpired:
            record.update(exit_code=None, error=f"timeout after {action.timeout_s}s")
            failed = True
        except OSError as exc:
            record.update(exit_code=None, error=str(exc))
            failed = True
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError:
            log.warning("cannot write reaction log %s", self._log_path, exc_info=True)
        with self._stat_lock:
            if failed:
                self._stats.failed += 1
                detail = record.get("error") or f"exit {record['exit_code']}"
                self._stats.last_error = f"{action.argv[0]}: {detail}"
            else:
                self._stats.fired += 1
        log.info("reaction executed %s -> %s", names, record.get("error") or f"exit {record['exit_code']}")

