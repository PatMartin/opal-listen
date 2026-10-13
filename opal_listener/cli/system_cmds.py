"""opal listen config|system"""

from __future__ import annotations

from pathlib import Path

import typer

from opal_listener.cli.client import CliError
from opal_listener.cli.output import console, done, print_json, render, render_kv, state
from opal_listener.models.config_schemas import ConfigBundle
from opal_listener.service.config_store import ConfigFileError, bundle_to_files, load_config_dir

BACKUP = "/api/v1/backup"
HEALTH = "/api/v1/health"

config_app = typer.Typer(help="Validate, back up, restore, export and import configuration.", no_args_is_help=True)
system_app = typer.Typer(help="Health checks and system information.", no_args_is_help=True)


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
            console.print(f"[red]error[/red]   {error}")
        for warning in report["warnings"]:
            console.print(f"[yellow]warning[/yellow] {warning}")
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
    result = state(ctx).api.post(f"{BACKUP}/restore/{filename}")
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
    out_dir: Path | None = typer.Option(None, "--out-dir", file_okay=False, help="Write the six YAML files here"),
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
    result = state(ctx).api.post(f"{BACKUP}/import", json=body)
    if state(ctx).json_output:
        print_json(result)
        return
    done(ctx, f"imported configuration (backup: {result['backup_filename']})")
    render_kv(ctx, result["counts"], "Imported")
    for warning in result["warnings"]:
        console.print(f"[yellow]warning[/yellow] {warning}")


STATUS_ICON = {"ok": "[green]✓[/green]", "warning": "[yellow]![/yellow]", "error": "[red]✗[/red]"}


@system_app.command("doctor")
def system_doctor(ctx: typer.Context) -> None:
    """Run startup health checks (SQLite, model, microphone, intents, slots ...). Exit 1 on errors."""
    report = state(ctx).api.get(f"{HEALTH}/")
    if state(ctx).json_output:
        print_json(report)
    else:
        for check in report["checks"]:
            console.print(f"{STATUS_ICON[check['status']]} {check['name']:<16} {check['detail']}")
        console.print(f"overall: {STATUS_ICON[report['status']]} {report['status']}")
    if report["status"] == "error":
        raise CliError("health check failed")


@system_app.command("devices")
def system_devices(ctx: typer.Context) -> None:
    """List audio input devices visible to the server."""
    devices = state(ctx).api.get(f"{HEALTH}/audio_devices")
    render(
        ctx,
        devices,
        ("index", "name", "max_input_channels", "default_sample_rate", "is_default"),
        "Input devices",
    )


@system_app.command("info")
def system_info(ctx: typer.Context) -> None:
    """Server name and version."""
    render_kv(ctx, state(ctx).api.get("/"))


@system_app.command("ping")
def system_ping(ctx: typer.Context) -> None:
    """Liveness probe."""
    result = state(ctx).api.get("/healthz")
    done(ctx, f"{state(ctx).api.base_url} is up", result)


@system_app.command("live")
def system_live(ctx: typer.Context) -> None:
    """OPAL liveness probe (cheap: process + database reachability)."""
    probe = state(ctx).api.get(f"{HEALTH}/live")
    done(ctx, f"status: {probe['status']}", probe)


@system_app.command("ready")
def system_ready(ctx: typer.Context) -> None:
    """OPAL readiness probe (full: configuration compiles, provider loads, ...)."""
    probe = state(ctx).api.get(f"{HEALTH}/ready")
    done(ctx, f"status: {probe['status']}", probe)
