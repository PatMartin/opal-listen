"""opal listen config|service"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import typer

from opal_listener.app_settings import get_app_settings
from opal_listener.cli.client import CliError
from opal_listener.cli.output import console, done, escape, print_json, render, render_kv, state
from opal_listener.models.config_schemas import ConfigBundle
from opal_listener.service.config_store import ConfigFileError, bundle_to_files, load_config_dir
from opal_listener.service.errors import NotFoundError
from opal_listener.service.lifecycle import ServiceProcessManager, stop_pid

BACKUP = "/api/v1/backup"
HEALTH = "/api/v1/health"

config_app = typer.Typer(help="Validate, back up, restore, export and import configuration.", no_args_is_help=True)
service_app = typer.Typer(help="Run, probe and inspect the server process.", no_args_is_help=True)


def _local_bundle(directory: Path) -> ConfigBundle:
    try:
        return load_config_dir(directory)
    except ConfigFileError as exc:
        raise CliError("invalid config files:\n" + "\n".join(exc.errors)) from exc


def _confirm(ctx: typer.Context, yes: bool, message: str) -> None:
    if not yes and not typer.confirm(message):
        raise typer.Abort()


@config_app.command("validate")
def config_validate(
    ctx: typer.Context,
    from_dir: Path | None = typer.Option(
        None,
        "--from-dir",
        exists=True,
        file_okay=False,
        help="Validate local YAML files instead of the server's",
    ),
) -> None:
    """Validate configuration (server config dir + database, or local YAML files). Exit 1 if invalid."""
    api = state(ctx).api
    if from_dir is not None:
        report = api.post(f"{BACKUP}/validate", json=_local_bundle(from_dir).model_dump(mode="json"))
    else:
        report = api.get(f"{BACKUP}/validate")
    if state(ctx).json_output:
        print_json(report)
    else:
        for error in report["errors"]:
            console.print(f"[red]error[/red]   {escape(error)}")
        for warning in report["warnings"]:
            console.print(f"[yellow]warning[/yellow] {escape(warning)}")
        console.print(
            "[green]configuration is valid[/green]" if report["valid"] else "[red]configuration is invalid[/red]"
        )
    if not report["valid"]:
        raise CliError("validation failed")


@config_app.command("backup")
def config_backup(ctx: typer.Context) -> None:
    """Create a SQLite backup on the server."""
    result = state(ctx).api.post(f"{BACKUP}/create")
    done(ctx, f"backup created: {result['filename']}", result)


@config_app.command("backups")
def config_backups(ctx: typer.Context) -> None:
    """List backups."""
    render(ctx, state(ctx).api.get(f"{BACKUP}/list"), ("filename", "size_bytes", "created_at"), "Backups")


@config_app.command("restore")
def config_restore(
    ctx: typer.Context,
    filename: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
) -> None:
    """Restore a backup (a safety backup is taken first; rolls back automatically on failure)."""
    _confirm(ctx, yes, f"Replace the current database with {filename}?")
    result = state(ctx).api.post_with_etag(f"{BACKUP}/restore/{filename}", etag_path=f"{BACKUP}/export")
    done(ctx, f"restored {filename} (previous state saved as {result.get('rollback_backup')})", result)


@config_app.command("download")
def config_download(
    ctx: typer.Context,
    filename: str,
    out: Path = typer.Option(Path("."), "--out", "-o", help="Target file or directory"),
) -> None:
    """Download a backup file."""
    content = state(ctx).api.get(f"{BACKUP}/download/{filename}")
    target = out / filename if out.is_dir() else out
    target.write_bytes(content if isinstance(content, bytes) else bytes(str(content), "utf-8"))
    done(ctx, f"saved {target}", {"status": "ok", "path": str(target)})


@config_app.command("delete-backup")
def config_delete_backup(
    ctx: typer.Context,
    filename: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
) -> None:
    """Delete a backup file."""
    _confirm(ctx, yes, f"Delete backup {filename}?")
    state(ctx).api.delete(f"{BACKUP}/{filename}")
    done(ctx, f"deleted {filename}")


@config_app.command("export")
def config_export(
    ctx: typer.Context,
    out_dir: Path | None = typer.Option(None, "--out-dir", file_okay=False, help="Write the seven YAML files here"),
) -> None:
    """Export configuration (JSON to stdout, or YAML files with --out-dir)."""
    bundle = ConfigBundle.model_validate(state(ctx).api.get(f"{BACKUP}/export"))
    if out_dir is None:
        print_json(bundle.model_dump(mode="json"))
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in bundle_to_files(bundle).items():
        (out_dir / filename).write_text(text, encoding="utf-8")
    done(ctx, f"exported configuration to {out_dir}", {"status": "ok", "path": str(out_dir)})


@config_app.command("import")
def config_import(
    ctx: typer.Context,
    from_dir: Path | None = typer.Option(
        None,
        "--from-dir",
        exists=True,
        file_okay=False,
        help="Import local YAML files (default: server ./config/)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
) -> None:
    """Replace configuration from YAML (validated first; automatic pre-import backup)."""
    body = _local_bundle(from_dir).model_dump(mode="json") if from_dir else None
    _confirm(ctx, yes, "Replace wakewords, intents, slots, profiles and settings?")
    result = state(ctx).api.post_with_etag(f"{BACKUP}/import", etag_path=f"{BACKUP}/export", json=body)
    if state(ctx).json_output:
        print_json(result)
        return
    done(ctx, f"imported configuration (backup: {result['backup_filename']})")
    render_kv(ctx, result["counts"], "Imported")
    for warning in result["warnings"]:
        console.print(f"[yellow]warning[/yellow] {escape(warning)}")


STATUS_ICON = {"ok": "[green]✓[/green]", "warning": "[yellow]![/yellow]", "error": "[red]✗[/red]"}


@service_app.command("doctor")
def service_doctor(ctx: typer.Context) -> None:
    """Run startup health checks (SQLite, model, microphone, intents, slots ...). Exit 1 on errors."""
    report = state(ctx).api.get(f"{HEALTH}/")
    if state(ctx).json_output:
        print_json(report)
    else:
        for check in report["checks"]:
            console.print(f"{STATUS_ICON[check['status']]} {escape(check['name']):<16} {escape(str(check['detail']))}")
        console.print(f"overall: {STATUS_ICON[report['status']]} {report['status']}")
    if report["status"] == "error":
        raise CliError("health check failed")


@service_app.command("devices")
def service_devices(ctx: typer.Context) -> None:
    """List audio input devices visible to the server."""
    devices = state(ctx).api.get(f"{HEALTH}/audio_devices")
    render(
        ctx,
        devices,
        ("index", "name", "max_input_channels", "default_sample_rate", "is_default"),
        "Input devices",
    )


@service_app.command("info")
def service_info(ctx: typer.Context) -> None:
    """Service identity: name, package version and API contract version."""
    render_kv(ctx, state(ctx).api.get("/api/v1/info/"))


@service_app.command("ping")
def service_ping(ctx: typer.Context) -> None:
    """Liveness probe."""
    result = state(ctx).api.get("/api/v1/info/ping")
    done(ctx, f"{state(ctx).api.base_url} is up", result)


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
