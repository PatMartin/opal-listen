from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import FileResponse

from opal_listener.models.config_schemas import ConfigBundle
from opal_listener.models.opal_standard import OPAL_422_RESPONSE
from opal_listener.models.schemas import BackupInfo, ConfigImportResult, ConfigValidationReport
from opal_listener.service import backup as backup_ops
from opal_listener.service.config_store import ConfigFileError, export_bundle, load_config_dir
from opal_listener.service.errors import NotFoundError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.nlu.engine import CompileReport
from opal_listener.service.runtime_config import ConfigInvalidError, compile_bundle
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


@router.get("/list", response_model=list[BackupInfo], responses=OPAL_422_RESPONSE)
def list_backups(svc: ListenerService = Depends(get_service)) -> list[BackupInfo]:
    return backup_ops.list_backups(svc.app_settings.backup_dir)


@router.post("/create", responses=OPAL_422_RESPONSE)
def create_backup(svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    return {"status": "created", "filename": svc.create_backup("manual")}


@router.get("/download/{filename}", response_class=FileResponse)
def download_backup(filename: str, svc: ListenerService = Depends(get_service)) -> FileResponse:
    path = backup_ops.safe_backup_path(svc.app_settings.backup_dir, filename)
    if not path.exists():
        raise NotFoundError(f"backup '{filename}' not found")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@router.post("/restore/{filename}")
def restore_backup(filename: str, svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    return svc.restore_backup(filename)


@router.delete("/{filename}", status_code=204)
def delete_backup(filename: str, svc: ListenerService = Depends(get_service)) -> Response:
    backup_ops.delete_backup(svc.app_settings.backup_dir, filename)
    return Response(status_code=204)


@router.get("/export", response_model=ConfigBundle, responses=OPAL_422_RESPONSE)
def export_config(svc: ListenerService = Depends(get_service)) -> ConfigBundle:
    """Export full configuration as JSON."""
    with svc.db.session() as session:
        return export_bundle(session)


@router.post("/import", response_model=ConfigImportResult)
def import_config(
    bundle: ConfigBundle | None = Body(default=None, description="Bundle to import; omit to use ./config/"),
    svc: ListenerService = Depends(get_service),
) -> ConfigImportResult:
    """Import configuration from YAML files in ./config/."""
    if bundle is None:
        try:
            bundle = load_config_dir(svc.app_settings.config_dir)
        except ConfigFileError as exc:
            raise ConfigInvalidError(CompileReport(errors=list(exc.errors))) from exc
    return svc.import_bundle(bundle)


@router.get("/validate", response_model=ConfigValidationReport, responses=OPAL_422_RESPONSE)
def validate_config(svc: ListenerService = Depends(get_service)) -> ConfigValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    sources: dict[str, bool] = {}
    try:
        file_bundle = load_config_dir(svc.app_settings.config_dir)
        report, _ = compile_bundle(file_bundle)
        errors += [f"config_dir: {e}" for e in report.errors]
        warnings += [f"config_dir: {w}" for w in report.warnings]
        sources["config_dir"] = report.ok
    except ConfigFileError as exc:
        errors += [f"config_dir: {e}" for e in exc.errors]
        sources["config_dir"] = False
    with svc.db.session() as session:
        report, _ = compile_bundle(export_bundle(session))
    errors += [f"database: {e}" for e in report.errors]
    warnings += [f"database: {w}" for w in report.warnings]
    sources["database"] = report.ok
    return ConfigValidationReport(valid=not errors, errors=errors, warnings=warnings, sources=sources)


@router.post(
    "/validate",
    response_model=ConfigValidationReport,
    operation_id="validate_listen_backup_payload",
)
def validate_listen_backup_payload(
    bundle: ConfigBundle, svc: ListenerService = Depends(get_service)
) -> ConfigValidationReport:
    """Validate a supplied backup/configuration bundle without any side effects."""
    report = svc.validate_bundle(bundle)
    report.sources = {"bundle": report.valid}
    return report
