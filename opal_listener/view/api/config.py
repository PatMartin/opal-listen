"""OPAL configuration snapshots: save, load and validate (kernel pathItems, v2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import select

from opal_listener.models.opal_standard import (
    OPAL_400_RESPONSE,
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_500_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
)
from opal_listener.models.schemas import (
    OpalConfigLoadRequest,
    OpalConfigLoadResult,
    OpalConfigSaveRequest,
    OpalConfigSaveResult,
    OpalConfigSnapshot,
    OpalConfigValidateRequest,
    OpalConfigValidation,
    OpalValidationIssue,
)
from opal_listener.models.tables import Setting
from opal_listener.service.concurrency import require_matching_etag
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.settings_registry import SETTINGS
from opal_listener.service.snapshots import (
    apply_snapshot,
    build_snapshot,
    config_revision,
    load_managed,
    save_managed,
    validate_snapshot,
)
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/config", tags=["config"])


def _issue(code: str, path: str, message: str) -> OpalValidationIssue:
    return OpalValidationIssue(code=code, path=path, message=message)


@router.post(
    "/save",
    operation_id="opal_config_save",
    response_model=OpalConfigSaveResult,
    responses={**OPAL_422_RESPONSE, **OPAL_500_RESPONSE},
)
def opal_config_save(
    response: Response,
    request: OpalConfigSaveRequest | None = None,
    svc: ListenerService = Depends(get_service),
) -> OpalConfigSaveResult:
    """Capture a consistent snapshot of persistent desired configuration.

    `destination: managed` atomically creates or replaces the single managed snapshot;
    `response` returns the envelope without storing it. Live configuration and the live
    revision are not mutated by this operation.
    """
    destination = request.destination if request else "managed"
    with svc.db.session() as session:
        snapshot = build_snapshot(session)
        revision = config_revision(session)
    managed = destination == "managed"
    if managed:
        save_managed(svc.app_settings.data_dir, snapshot)
    response.headers["ETag"] = revision
    return OpalConfigSaveResult(
        snapshot=snapshot,
        managed=managed,
        revision=revision,
        destination=destination,
        destination_ref="managed" if managed else None,
    )


@router.post(
    "/load",
    operation_id="opal_config_load",
    response_model=OpalConfigLoadResult,
    responses={
        **OPAL_404_RESPONSE,
        **OPAL_409_RESPONSE,
        **OPAL_PRECONDITION_RESPONSES,
        **OPAL_422_RESPONSE,
        **OPAL_500_RESPONSE,
    },
)
def opal_config_load(
    response: Response,
    request: OpalConfigLoadRequest | None = None,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> OpalConfigLoadResult:
    """Atomically replace persistent desired configuration.

    `snapshot` omitted (or body `{}`) loads the managed snapshot; an inline envelope loads
    that candidate instead. If-Match must equal the live-configuration revision.
    """
    inline = request.snapshot if request else None
    warnings: list[OpalValidationIssue] = []
    with svc.config_change() as session:
        require_matching_etag(if_match, config_revision(session), "configuration")
        snapshot = inline if inline is not None else load_managed(svc.app_settings.data_dir)
        _check_snapshot_semantics(snapshot)
        applied = apply_snapshot(session, snapshot)
    after_revision = svc.config_etag()
    pending = [
        key
        for key in snapshot.settings
        if key in SETTINGS and svc.setting_pending_restart(key, snapshot.settings[key])
    ]
    if pending:
        warnings.append(
            _issue("restart_required", "settings", "settings changes take effect after service restart")
        )
    response.headers["ETag"] = after_revision
    return OpalConfigLoadResult(
        applied=True,
        pending_restart_fields=pending,
        warnings=warnings or None,
        revision=after_revision,
        applied_counts=applied,
    )


def _check_snapshot_semantics(snapshot: OpalConfigSnapshot) -> None:
    """Reject envelopes the loose model admits but the contract forbids (wrong module/version)."""
    errors, _ = validate_snapshot(snapshot.model_dump(mode="json"))
    if errors:
        raise InvalidRequestError(
            "snapshot is invalid: " + "; ".join(f"{i.path}: {i.message}" for i in errors)
        )


@router.post(
    "/validate",
    operation_id="opal_config_validate",
    response_model=OpalConfigValidation,
    responses={**OPAL_400_RESPONSE, **OPAL_422_RESPONSE},
)
def opal_config_validate(
    request: OpalConfigValidateRequest | None = None,
    svc: ListenerService = Depends(get_service),
) -> OpalConfigValidation:
    """Report structured issues for a candidate snapshot or (omitted) the live desired
    configuration. Never mutates configuration."""
    raw = request.snapshot if request else None
    if raw is not None:
        if not isinstance(raw, dict):
            return OpalConfigValidation(
                valid=False,
                errors=[_issue("invalid_field", "snapshot", "snapshot must be a JSON object")],
                warnings=[],
            )
        errors, warnings = validate_snapshot(raw)
        return OpalConfigValidation(valid=not errors, errors=errors, warnings=warnings)
    with svc.db.session() as session:
        stored = {row.key: row.value for row in session.exec(select(Setting)).all()}
    warnings = [
        _issue("unknown_setting", f"settings.{key}", f"unknown setting '{key}'")
        for key in stored
        if key not in SETTINGS
    ]
    return OpalConfigValidation(valid=True, errors=[], warnings=warnings)
