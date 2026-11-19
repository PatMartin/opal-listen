"""opal listen config|service"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml

from opal_listener.app_settings import get_app_settings
from opal_listener.cli.client import CliError
from opal_listener.cli.output import console, done, escape, print_json, render, render_kv, state
from opal_listener.service.errors import NotFoundError
from opal_listener.service.lifecycle import ServiceProcessManager, stop_pid

CONFIG = "/api/v1/config"
HEALTH = "/api/v1/health"

config_app = typer.Typer(
    help="Save, load and validate persistent configuration snapshots.", no_args_is_help=True
)
service_app = typer.Typer(help="Run, probe and inspect the server process.", no_args_is_help=True)
info_app = typer.Typer(help="Service information and health.", no_args_is_help=True)


def _confirm(ctx: typer.Context, yes: bool, message: str) -> None:
    if not yes and not typer.confirm(message):
        raise typer.Abort()


def _read_snapshot(path: Path) -> dict[str, Any]:
    """Read a snapshot envelope from JSON or YAML (contract `local_in` binding for --in)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text) if path.suffix.lower() in (".yaml", ".yml") else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise CliError(f"{path} is not valid JSON/YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError(f"{path} does not contain a snapshot object")
    return data


def _config_revision(api: Any) -> str:
    """Live-configuration revision for the config load If-Match (non-mutating probe)."""
    return api.post(f"{CONFIG}/save", json={"destination": "response"})["revision"]


@config_app.command("save")
def config_save(
    ctx: typer.Context,
    out: Path | None = typer.Option(
        None, "--out", "-o", dir_okay=False, help="Write the snapshot envelope to FILE"
    ),
) -> None:
    """Capture a configuration snapshot.

    Without --out the single managed snapshot on the server is replaced; with --out the
    envelope is written to FILE and the managed snapshot is left untouched.
    """
    api = state(ctx).api
    if out is None:
        result = api.post(f"{CONFIG}/save", json={"destination": "managed"})
        done(
            ctx,
            f"snapshot saved to managed store (revision {result['revision']})",
            result,
        )
        return
    result = api.post(f"{CONFIG}/save", json={"destination": "response"})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result["snapshot"], indent=2) + "\n", encoding="utf-8")
    done(ctx, f"snapshot written to {out} (revision {result['revision']})", {"path": str(out)})


@config_app.command("load")
def config_load(
    ctx: typer.Context,
    in_file: Path | None = typer.Option(
        None, "--in", "-i", exists=True, dir_okay=False, help="Snapshot file to load (JSON or YAML)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
) -> None:
    """Atomically replace desired configuration from the managed snapshot or --in FILE."""
    api = state(ctx).api
    body: dict[str, Any] = {}
    if in_file is not None:
        body = {"snapshot": _read_snapshot(in_file)}
    _confirm(ctx, yes, "Replace wakewords, intents, slots, profiles, personas and settings?")
    result = api.post(
        f"{CONFIG}/load", json=body, headers={"If-Match": _config_revision(api)}
    )
    if state(ctx).json_output:
        print_json(result)
        return
    done(ctx, "configuration loaded", result)
    for field in result.get("pending_restart_fields") or []:
        console.print(f"[yellow]pending restart[/yellow] {escape(field)}")
    for issue in result.get("warnings") or []:
        console.print(f"[yellow]warning[/yellow] {escape(issue['path'])}: {escape(issue['message'])}")


@config_app.command("validate")
def config_validate(
    ctx: typer.Context,
    in_file: Path | None = typer.Option(
        None, "--in", "-i", exists=True, dir_okay=False, help="Snapshot file to check (JSON or YAML)"
    ),
) -> None:
    """Validate the live configuration or a candidate snapshot file. Exit 1 if invalid."""
    api = state(ctx).api
    body: dict[str, Any] = {}
    if in_file is not None:
        body = {"snapshot": _read_snapshot(in_file)}
    report = api.post(f"{CONFIG}/validate", json=body)
    if state(ctx).json_output:
        print_json(report)
    else:
        for issue in report["errors"]:
            console.print(f"[red]error[/red]   {escape(issue['path'])}: {escape(issue['message'])}")
        for issue in report["warnings"]:
            console.print(f"[yellow]warning[/yellow] {escape(issue['path'])}: {escape(issue['message'])}")
        console.print(
            "[green]configuration is valid[/green]"
            if report["valid"]
            else "[red]configuration is invalid[/red]"
        )
    if not report["valid"]:
        raise CliError("validation failed")


STATUS_ICON = {"ok": "[green]✓[/green]", "warning": "[yellow]![/yellow]", "degraded": "[yellow]![/yellow]", "error": "[red]✗[/red]"}


@info_app.command("health")
def info_health(ctx: typer.Context) -> None:
    """Aggregate health report: identity, status, live/ready and module diagnostics (details)."""
    report = state(ctx).api.get(f"{HEALTH}/")
    if state(ctx).json_output:
        print_json(report)
    else:
        details = report.get("details") or {}
        for check in details.get("checks", []):
            console.print(f"{STATUS_ICON[check['status']]} {escape(check['name']):<16} {escape(str(check['detail']))}")
        devices = details.get("audio_devices") or []
        if devices:
            render(ctx, devices, ("index", "name", "max_input_channels", "default_sample_rate", "is_default"), "Input devices")
        console.print(f"overall: {STATUS_ICON.get(report['status'], report['status'])} {report['status']}")
        console.print(f"{report['name']} {report['version']} (api {report['api_version']}) live={report['live']} ready={report['ready']}")
    if report["status"] == "error":
        raise CliError("health check failed")


@service_app.command("live")
def service_live(ctx: typer.Context) -> None:
    """OPAL liveness probe (cheap: process + database reachability)."""
    probe = state(ctx).api.get(f"{HEALTH}/live")
    done(ctx, f"status: {probe['status']}", probe)


@service_app.command("ready")
def service_ready(ctx: typer.Context) -> None:
    """OPAL readiness probe (full: configuration compiles, provider loads, ...)."""
    probe = state(ctx).api.get(f"{HEALTH}/ready")
    done(ctx, f"status: {probe['status']}", probe)


# --- server process lifecycle --------------------------------------------------------------------
#
# `service start|stop|log` are REST-first (the kernel service ops act on the managed server
# process server-side); when the API is unreachable they fall back to the same local
# ServiceProcessManager the endpoints use — that is what lets `service start` cold-boot a
# server that isn't running yet.

LOG_DIR = Path("logs")


def _manager(host: str | None = None, port: int | None = None, data_dir: Path | None = None) -> ServiceProcessManager:
    settings = get_app_settings()
    updates = {k: v for k, v in (("host", host), ("port", port), ("data_dir", data_dir)) if v is not None}
    return ServiceProcessManager(settings.model_copy(update=updates) if updates else settings, LOG_DIR)


def launch_server(host: str, port: int, data_dir: Path | None, replace: bool = True) -> tuple[int, int | None]:
    """Spawn `python -m opal_listener` detached via the shared process manager. Returns
    (launcher pid, replaced pid). Used by `gui`; `service start` prefers the REST op."""
    manager = _manager(host, port, data_dir)
    previous = manager.tracked_pid() if replace else None
    if previous is not None:
        stop_pid(previous)
    manager.pid_file.unlink(missing_ok=True)
    proc = manager.launch()
    return proc.pid, previous


def _rest(ctx: typer.Context, method: str, path: str, **kwargs: Any) -> dict | None:
    """Call the REST op; returns the JSON body on 2xx, raises CliError on API errors, and returns
    None when the API is unreachable (caller falls back to local process management)."""
    try:
        response = state(ctx).api.http.request(method, path, **kwargs)
    except httpx.HTTPError:
        return None
    if response.status_code == 404:
        raise NotFoundError(path)
    if response.status_code >= 400:
        raise CliError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def _status_line(status: dict[str, Any]) -> str:
    pid = f"pid {status['pid']}" if status.get("pid") else "no pid"
    prev = f" (replaced pid {status['previous_pid']})" if status.get("previous_pid") else ""
    return f"server {status['state']} ({pid}){prev} — log: {status['log_file']}"


@service_app.command("start")
def service_start(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port", min=1, max=65535),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="OPAL_LISTENER_DATA_DIR for the server"),
    append: bool = typer.Option(False, "--append", help="Append to the log file instead of truncating it"),
) -> None:
    """Start the managed server (restarts one already running), logging to logs/opal_listen.log."""
    status = _rest(ctx, "POST", "/api/v1/service/start", json={"append": append})
    if status is None:
        status, _ = _manager(host, port, data_dir).start(append=append)
    done(ctx, _status_line(status), status)


@service_app.command("stop")
def service_stop(ctx: typer.Context) -> None:
    """Stop the managed server started by `service start`, if it is running."""
    status = _rest(ctx, "POST", "/api/v1/service/stop")
    if status is None:
        status, _ = _manager().stop()
    done(ctx, _status_line(status), status)


@service_app.command("log")
def service_log(ctx: typer.Context, lines: int = typer.Option(10, "--lines", "-n", min=1)) -> None:
    """Show the last lines of the managed server's log (default 10)."""
    try:
        body = _rest(ctx, "GET", "/api/v1/service/log", params={"lines": lines})
    except NotFoundError:
        body = None
    if body is None:
        body = _manager().tail_log(lines)
    if body is None:
        done(ctx, f"no log file at {LOG_DIR / 'opal_listen.log'}")
        return
    if state(ctx).json_output:
        print_json(body["lines"])
    else:
        for line in body["lines"]:
            console.print(line)
