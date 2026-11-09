"""opal listen config|service"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer

from opal_listener.cli.client import CliError
from opal_listener.cli.output import console, done, escape, print_json, render, render_kv, state
from opal_listener.models.config_schemas import ConfigBundle
from opal_listener.service.config_store import ConfigFileError, bundle_to_files, load_config_dir

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

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "opal_listen.log"
PID_FILE = LOG_DIR / "opal_listen.pid"


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) is unreliable on Windows (OpenProcess(PROCESS_ALL_ACCESS) fails with
        # WinError 87); query the exit code with limited rights instead.
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # alive but not ours to signal
    return True


def _stop_pid(pid: int) -> None:
    """Stop the recorded pid (the server writes its own pid, so this is the uvicorn process
    itself, not the python-launcher shim). taskkill /T also takes any children."""
    if os.name == "nt":
        with contextlib.suppress(OSError):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(40):  # ~4s to exit cleanly
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def _stop_tracked() -> int | None:
    """Stop the server recorded in the pidfile, if it is still alive. Returns its pid."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return None
    if _pid_alive(pid):
        _stop_pid(pid)
    PID_FILE.unlink(missing_ok=True)
    return pid


def launch_server(host: str, port: int, data_dir: Path | None, replace: bool = True) -> tuple[int, int | None]:
    """Spawn `python -m opal_listener` detached, logging to LOG_FILE (truncated). The server writes
    its real pid to PID_FILE (see OPAL_LISTENER_PID_FILE). Returns (launcher pid, replaced pid)."""
    replaced = _stop_tracked() if replace else None
    LOG_DIR.mkdir(exist_ok=True)
    env = {**os.environ, "OPAL_LISTENER_HOST": host, "OPAL_LISTENER_PORT": str(port)}
    if data_dir is not None:
        env["OPAL_LISTENER_DATA_DIR"] = str(data_dir)
    log = LOG_FILE.open("wb")  # truncated on every start
    kwargs: dict[str, object] = (
        {"start_new_session": True}
        if os.name != "nt"
        else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    )
    PID_FILE.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "opal_listener"],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**env, "OPAL_LISTENER_PID_FILE": str(PID_FILE.resolve())},
        **kwargs,
    )
    return proc.pid, replaced


@service_app.command("start")
def service_start(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port", min=1, max=65535),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="OPAL_LISTENER_DATA_DIR for the server"),
) -> None:
    """Start the server in the background (replaces a running one), logging to logs/opal_listen.log."""
    pid, replaced = launch_server(host, port, data_dir)
    note = f" (replaced pid {replaced})" if replaced else ""
    done(ctx, f"server started (pid {pid}){note} — log: {LOG_FILE}", {"pid": pid})


@service_app.command("stop")
def service_stop(ctx: typer.Context) -> None:
    """Stop the background server started by `service start`, if it is running."""
    pid = _stop_tracked()
    if pid is None:
        done(ctx, "no managed server running")
    else:
        done(ctx, f"server stopped (pid {pid})", {"pid": pid})


@service_app.command("log")
def service_log(ctx: typer.Context, lines: int = typer.Option(10, "--lines", "-n", min=1)) -> None:
    """Show the last lines of logs/opal_listen.log (default 10)."""
    if not LOG_FILE.exists():
        done(ctx, f"no log file at {LOG_FILE}")
        return
    tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    if state(ctx).json_output:
        print_json(tail)
    else:
        for line in tail:
            console.print(line)
