from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, Path, Response
from fastapi.responses import FileResponse

from opal_listener.models.config_schemas import ConfigBundle
from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_500_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
)
from opal_listener.models.schemas import (
    OpalBackupCreateResponse,
    OpalBackupInfo,
    OpalImportResult,
    OpalValidationReport,
)
from opal_listener.service import backup as backup_ops
from opal_listener.service.concurrency import require_matching_etag
from opal_listener.service.config_store import ConfigFileError, config_etag, export_bundle, load_config_dir
from opal_listener.service.errors import NotFoundError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.nlu.engine import CompileReport
from opal_listener.service.runtime_config import ConfigInvalidError, compile_bundle
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])

_FILENAME = Path(min_length=1, max_length=255)


@router.get("/list", response_model=list[OpalBackupInfo], responses=OPAL_422_RESPONSE, operation_id="opal_backup_list")
def opal_backup_list(svc: ListenerService = Depends(get_service)) -> list[OpalBackupInfo]:
    return backup_ops.list_backups(svc.app_settings.backup_dir)


@router.post(
    "/create",
    status_code=201,
    response_model=OpalBackupCreateResponse,
    responses={**OPAL_422_RESPONSE, **OPAL_500_RESPONSE},
    operation_id="opal_backup_create",
)
def opal_backup_create(response: Response, svc: ListenerService = Depends(get_service)) -> OpalBackupCreateResponse:
    filename = svc.create_backup("manual")
    response.headers["Location"] = f"/api/v1/backup/download/{filename}"
    return OpalBackupCreateResponse(filename=filename, message=f"backup '{filename}' created")


@router.get(
    "/download/{filename}",
    response_class=FileResponse,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_backup_download",
)
def opal_backup_download(filename: str = _FILENAME, svc: ListenerService = Depends(get_service)) -> FileResponse:
    path = backup_ops.safe_backup_path(svc.app_settings.backup_dir, filename)
    if not path.exists():
        raise NotFoundError(f"backup '{filename}' not found")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@router.post(
    "/restore/{filename}",
    response_model=OpalImportResult,
    responses={
        **OPAL_404_RESPONSE,
        **OPAL_409_RESPONSE,
        **OPAL_422_RESPONSE,
        **OPAL_500_RESPONSE,
        **OPAL_PRECONDITION_RESPONSES,
    },
    operation_id="opal_backup_restore",
)
def opal_backup_restore(
    filename: str = _FILENAME,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> dict[str, str]:
    with svc.db.session() as session:
        require_matching_etag(if_match, config_etag(session), "configuration")
    return svc.restore_backup(filename)


@router.delete(
    "/{filename}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_backup_delete",
)
def opal_backup_delete(filename: str = _FILENAME, svc: ListenerService = Depends(get_service)) -> Response:
    backup_ops.delete_backup(svc.app_settings.backup_dir, filename)
    return Response(status_code=204)


@router.get(
    "/export",
    response_model=ConfigBundle,
    responses={**OPAL_422_RESPONSE, **OPAL_500_RESPONSE},
    operation_id="opal_backup_export",
)
def opal_backup_export(response: Response, svc: ListenerService = Depends(get_service)) -> ConfigBundle:
    """Export full configuration as JSON."""
    with svc.db.session() as session:
        bundle = export_bundle(session)
        response.headers["ETag"] = config_etag(session)
        return bundle


@router.post(
    "/import",
    response_model=OpalImportResult,
    responses={**OPAL_409_RESPONSE, **OPAL_422_RESPONSE, **OPAL_500_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="opal_backup_import",
)
def opal_backup_import(
    bundle: ConfigBundle | None = Body(default=None, description="Bundle to import; omit to use ./config/"),
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> OpalImportResult:
    """Import configuration from YAML files in ./config/."""
    with svc.db.session() as session:
        require_matching_etag(if_match, config_etag(session), "configuration")
    if bundle is None:
        try:
            bundle = load_config_dir(svc.app_settings.config_dir)
        except ConfigFileError as exc:
            raise ConfigInvalidError(CompileReport(errors=list(exc.errors))) from exc
    return svc.import_bundle(bundle)


@router.get(
    "/validate",
    response_model=OpalValidationReport,
    responses=OPAL_422_RESPONSE,
    operation_id="opal_backup_validate_stored",
)
def opal_backup_validate_stored(svc: ListenerService = Depends(get_service)) -> OpalValidationReport:
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
    return OpalValidationReport(valid=not errors, errors=errors, warnings=warnings, sources=sources)


@router.post(
    "/validate",
    response_model=OpalValidationReport,
    responses=OPAL_422_RESPONSE,
    operation_id="opal_backup_validate",
)
def opal_backup_validate(bundle: ConfigBundle, svc: ListenerService = Depends(get_service)) -> OpalValidationReport:
    """Validate a supplied backup/configuration bundle without any side effects."""
    report = svc.validate_bundle(bundle)
    report.sources = {"bundle": report.valid}
    return report
