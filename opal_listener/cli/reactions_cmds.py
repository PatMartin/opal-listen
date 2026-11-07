"""opal listen reactions — manage file-provisioned intent reactions.

These commands edit <config_dir>/reactions.yaml directly on the local filesystem —
deliberately NOT through the REST API: an API-mutable shell command would be a remote-
code-execution vector, so arming stays file-only. The running service notices file
changes automatically (mtime check on each match). All commands work with the service
stopped, except `test --text`, which calls POST /recognition/test_text for a real
recognition payload.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

import typer
import yaml
from pydantic import ValidationError
from rich.text import Text

from opal_listener.app_settings import AppSettings
from opal_listener.cli.client import CliError
from opal_listener.cli.output import console, done, escape, print_json, render, render_kv, state
from opal_listener.service.reactions import (
    OPERATORS,
    ReactionCondition,
    ReactionsFile,
    check_condition,
    record_outcome,
    run_action,
)

reactions_app = typer.Typer(
    help="Manage intent reactions (file-provisioned commands fired on intent match).",
    no_args_is_help=True,
)

_FILE_HEADER = """\
# Managed by `opal listen reactions` — edits are safe but comments are not preserved.
# Reactions fire only on live-mic matches while `reactions_enabled` is true:
#   opal listen settings set reactions_enabled true
# Schema: docs/USER_GUIDE.md section 4.5 (intent reactions).
"""

TEST_TEXT = "/api/v1/recognition/test_text"


def config_path() -> Path:
    return AppSettings().config_dir / "reactions.yaml"


def log_path() -> Path:
    return AppSettings().data_dir / "reaction_log.jsonl"


def _load_raw() -> tuple[Path, list[dict[str, Any]]]:
    path = config_path()
    if not path.is_file():
        return path, []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CliError(f"{path}: invalid YAML: {exc}") from exc
    raw = data.get("reactions") if isinstance(data, dict) else None
    if raw is None:
        return path, []
    if not isinstance(raw, list):
        raise CliError(f"{path}: 'reactions' must be a list")
    return path, raw


def _save(path: Path, reactions: list[dict[str, Any]]) -> None:
    try:
        validated = ReactionsFile.model_validate({"reactions": reactions})
    except ValidationError as exc:
        raise CliError("invalid reactions file (write aborted):\n" + "\n".join(str(e) for e in exc.errors())) from exc
    body = yaml.safe_dump(
        {"reactions": [r.model_dump(mode="json", exclude_none=True) for r in validated.reactions]},
        sort_keys=False,
        allow_unicode=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_FILE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)


def _find(reactions: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for r in reactions:
        if r.get("name") == name:
            return r
    raise CliError(f"no reaction named '{name}' (see: reactions list)")


def _parse_cond(raw: str) -> dict[str, Any]:
    parts = raw.split(None, 2)
    if len(parts) < 2:
        raise CliError(f"--cond needs 'path operator [value]', got '{raw}'")
    path, operator = parts[0], parts[1]
    if operator not in OPERATORS:
        raise CliError(f"unknown operator '{operator}' (one of: {', '.join(sorted(OPERATORS))})")
    if operator == "exists":
        if len(parts) > 2:
            raise CliError("'exists' takes no value")
        return {"path": path, "operator": operator}
    if len(parts) < 3:
        raise CliError(f"operator '{operator}' needs a value in '{raw}'")
    return {"path": path, "operator": operator, "value": yaml.safe_load(parts[2])}


def _parse_env(pairs: list[str] | None) -> dict[str, str] | None:
    if not pairs:
        return None
    env: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CliError(f"--env expects KEY=VALUE, got '{pair}'")
        k, v = pair.split("=", 1)
        env[k.strip()] = v
    return env


def _split_run(run: str) -> list[str]:
    """Shell-style split. On Windows posix=False keeps backslashes in paths; the
    quote chars it preserves are stripped from each token."""
    if os.name == "nt":
        return [t.strip('"').strip("'") for t in shlex.split(run, posix=False)]
    return shlex.split(run)


def _actions(
    runs: list[str] | None, timeout: float, cwd: str | None, env: list[str] | None
) -> list[dict[str, Any]]:
    env_map = _parse_env(env)
    actions = []
    for run in runs or []:
        argv = _split_run(run)
        if not argv:
            raise CliError("empty --run command")
        action: dict[str, Any] = {"type": "command", "argv": argv, "timeout_s": timeout}
        if cwd is not None:
            action["cwd"] = cwd
        if env_map:
            action["env"] = env_map
        actions.append(action)
    return actions


def _conditions(conds: list[str] | None) -> list[dict[str, Any]]:
    return [_parse_cond(c) for c in conds or []]


def _apply_action_options(
    action: dict[str, Any], timeout: float | None, cwd: str | None, env: dict[str, str] | None
) -> None:
    if timeout is not None:
        action["timeout_s"] = timeout
    if cwd is not None:
        action["cwd"] = cwd or None
    if env is not None:
        action["env"] = env


def _summary(reaction: dict[str, Any]) -> dict[str, Any]:
    actions = reaction.get("actions") or []
    argv0 = " && ".join(" ".join(a.get("argv", ["?"])[:1]) for a in actions) if actions else "-"
    return {
        "name": reaction.get("name"),
        "enabled": reaction.get("enabled", True),
        "conditions": len(reaction.get("conditions") or []),
        "actions": argv0 + (f" (+{len(actions)-1} more)" if len(actions) > 1 else ""),
    }


_COND_OPT = typer.Option(None, "--cond", help="'path operator value' (repeatable); ops: "
                                             "eq ne gt gte lt lte in not_in exists contains")
_RUN_OPT = typer.Option(None, "--run", help="Command line to execute (repeatable; one action each). "
                                            "Receives the match result JSON on stdin.")
_TIMEOUT_OPT = typer.Option(None, "--timeout", help="Per-action timeout in seconds (1..3600)")
_CWD_OPT = typer.Option(None, "--cwd", help="Working directory for the action")
_ENV_OPT = typer.Option(None, "--env", help="KEY=VALUE env overlay for the action (repeatable)")


@reactions_app.command("path")
def reactions_path(ctx: typer.Context) -> None:
    """Print the reactions file and execution log locations this CLI manages."""
    render_kv(ctx, {"reactions_file": str(config_path()), "execution_log": str(log_path())})


@reactions_app.command("list")
def reactions_list(ctx: typer.Context) -> None:
    """List reactions."""
    _, reactions = _load_raw()
    data = reactions if state(ctx).json_output else [_summary(r) for r in reactions]
    render(ctx, data, ("name", "enabled", "conditions", "actions"), "Reactions")


@reactions_app.command("get")
def reactions_get(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Show one reaction in full."""
    _, reactions = _load_raw()
    render_kv(ctx, _find(reactions, name), f"Reaction '{name}'")


@reactions_app.command("add")
def reactions_add(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    cond: list[str] | None = _COND_OPT,
    run: list[str] | None = _RUN_OPT,
    timeout: float = typer.Option(15.0, "--timeout", min=1.0, max=3600.0),
    cwd: str | None = _CWD_OPT,
    env: list[str] | None = _ENV_OPT,
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    """Arm a new reaction (writes reactions.yaml; service hot-reloads it)."""
    path, reactions = _load_raw()
    if any(r.get("name") == name for r in reactions):
        raise CliError(f"reaction '{name}' already exists")
    actions = _actions(run, timeout, cwd, env)
    if not actions:
        raise CliError("at least one --run is required")
    reaction = {"name": name, "enabled": enabled, "conditions": _conditions(cond), "actions": actions}
    _save(path, reactions + [reaction])
    done(ctx, f"reaction '{name}' added — enable firing with: settings set reactions_enabled true", reaction)


@reactions_app.command("update")
def reactions_update(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    rename: str | None = typer.Option(None, "--name", help="Rename the reaction"),
    cond: list[str] | None = _COND_OPT,
    clear_conditions: bool = typer.Option(False, "--clear-conditions",
                                        help="Drop all conditions (fire on any matched intent)"),
    run: list[str] | None = _RUN_OPT,
    timeout: float | None = _TIMEOUT_OPT,
    cwd: str | None = _CWD_OPT,
    env: list[str] | None = _ENV_OPT,
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a reaction. --cond/--run replace all conditions/actions when given;
    --timeout/--cwd/--env apply to every existing command action otherwise."""
    path, reactions = _load_raw()
    target = _find(reactions, name)
    changed = False
    if rename is not None:
        if rename != name and any(r.get("name") == rename for r in reactions):
            raise CliError(f"reaction '{rename}' already exists")
        target["name"] = rename
        changed = True
    if enabled is not None:
        target["enabled"] = enabled
        changed = True
    if cond:
        target["conditions"] = _conditions(cond)
        changed = True
    elif clear_conditions:
        target["conditions"] = []
        changed = True
    if run:
        target["actions"] = _actions(run, timeout or 15.0, cwd, env)
        changed = True
    elif timeout is not None or cwd is not None or env is not None:
        for action in target.get("actions") or []:
            if action.get("type", "command") == "command":
                _apply_action_options(action, timeout, cwd, _parse_env(env))
        changed = True
    if not changed:
        raise CliError("nothing to update (pass at least one option)")
    _save(path, reactions)
    done(ctx, f"reaction '{target['name']}' updated", target)


@reactions_app.command("remove")
def reactions_remove(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Disarm a reaction."""
    path, reactions = _load_raw()
    _find(reactions, name)
    _save(path, [r for r in reactions if r.get("name") != name])
    done(ctx, f"reaction '{name}' removed")


@reactions_app.command("enable")
def reactions_enable(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Enable a reaction."""
    _toggle(ctx, name, True)


@reactions_app.command("disable")
def reactions_disable(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Disable a reaction without deleting it."""
    _toggle(ctx, name, False)


def _toggle(ctx: typer.Context, name: str, enabled: bool) -> None:
    path, reactions = _load_raw()
    _find(reactions, name)["enabled"] = enabled
    _save(path, reactions)
    done(ctx, f"reaction '{name}' {'enabled' if enabled else 'disabled'}")


@reactions_app.command("test")
def reactions_test(
    ctx: typer.Context,
    text: str | None = typer.Option(None, "--text", help="Run a real utterance through test_text"),
    payload: str | None = typer.Option(None, "--payload", help="Result JSON to evaluate"),
    payload_file: Path | None = typer.Option(None, "--file", help="YAML/JSON file with a result payload"),
    execute: bool = typer.Option(False, "--execute", help="Actually run the matching commands"),
) -> None:
    """Dry-run: evaluate reactions against a payload without firing (unless --execute).

    Exactly one of --text (real pipeline, needs the service), --payload or --file."""
    given = [s is not None for s in (text, payload, payload_file)]
    if sum(given) != 1:
        raise CliError("pass exactly one of --text, --payload or --file")
    if text is not None:
        result_payload: dict[str, Any] = state(ctx).api.post(
            TEST_TEXT, json={"text": text}, params={"save_log": False}
        )
    elif payload is not None:
        result_payload = yaml.safe_load(payload)
    else:
        result_payload = yaml.safe_load(payload_file.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    if not isinstance(result_payload, dict):
        raise CliError("payload must be a JSON/YAML object with result fields (intent_name, slot_values, ...)")

    _, reactions = _load_raw()
    if not reactions:
        if state(ctx).json_output:
            print_json([])
        else:
            console.print(f"[dim]no reactions armed in {escape(str(config_path()))}[/dim]")
        return
    report = [
        {"name": r.get("name"), "enabled": r.get("enabled", True),
         "match": bool(r.get("enabled", True)) and all(check_condition(c, result_payload) for c in _conds_of(r)),
         "failed_conditions": [c.model_dump() for c in _conds_of(r) if not check_condition(c, result_payload)]}
        for r in reactions
    ]
    executions: list[dict[str, Any]] = []
    if execute:
        for r in reactions:
            if not next(e["match"] for e in report if e["name"] == r.get("name")):
                continue
            for action in ReactionsFile.model_validate({"reactions": [r]}).reactions[0].actions:
                executions.append(run_action(action, result_payload, str(r.get("name")), log_path(), origin="cli"))

    if state(ctx).json_output:
        print_json({"reactions": report, "executions": executions} if execute else report)
        return
    for entry in report:
        name = escape(str(entry["name"]))
        if not entry["enabled"]:
            console.print(f"[dim]-[/dim] {name} — disabled")
        elif entry["failed_conditions"]:
            why = ", ".join(f"{c['path']} {c['operator']} {c['value']!r}" for c in entry["failed_conditions"])
            console.print(f"[red]✗[/red] {name} — no match ({escape(why)})")
        else:
            actions = len(_find(reactions, entry["name"]).get("actions") or [])
            console.print(f"[green]✓[/green] {name} — would fire {actions} action(s)")
    for record in executions:
        line = f"  {record['reactions']}: {' '.join(record['argv'])} -> {record_outcome(record)} ({record['duration_ms']} ms)"
        if record.get("stderr_tail"):
            line += f"\n    stderr: {record['stderr_tail'].strip()[-300:]}"
        console.print(Text(line))
    if executions:
        console.print(f"[dim]journaled to {escape(str(log_path()))} (origin: cli)[/dim]")


def _conds_of(reaction: dict[str, Any]) -> list[ReactionCondition]:
    return [ReactionCondition.model_validate(c) for c in reaction.get("conditions") or []]


@reactions_app.command("log")
def reactions_log(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", min=1, help="Show the last N executions"),
) -> None:
    """Show recent executions from <data_dir>/reaction_log.jsonl.

    An absent journal is a normal state (nothing has fired yet), not an error —
    matches how `logs list` renders an empty table."""
    path = log_path()
    if not path.is_file():
        if state(ctx).json_output:
            print_json([])
        else:
            console.print(f"[dim]no reaction executions recorded yet ({escape(str(path))})[/dim]")
        return
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ][-limit:]
    if state(ctx).json_output:
        print_json(records)
        return
    for r in records:
        console.print(Text(
            f"{r.get('ts')}  {r.get('origin', 'service'):<7}  {r.get('reactions')}  "
            f"{' '.join(r.get('argv') or [])} -> "
            f"{r.get('error') or 'exit ' + str(r.get('exit_code'))}  ({r.get('duration_ms')} ms)"
        ))
    console.print(f"[dim]{len(records)} execution(s) — full log: {escape(str(path))}[/dim]")
