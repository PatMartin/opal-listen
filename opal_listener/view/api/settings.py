"""Atomic portable settings: collection GET/PATCH plus per-key GET/PUT/DELETE (OPAL v1.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Path, Response
from sqlmodel import select

from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
    OpalSettingsMap,
)
from opal_listener.models.schemas import SettingRead, SettingUpdate
from opal_listener.models.tables import Setting, utc_now
from opal_listener.service.concurrency import compute_etag, require_matching_etag
from opal_listener.service.errors import InvalidRequestError, NotFoundError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.settings_registry import (
    SETTINGS,
    SettingValueError,
    parse_setting,
    to_storage,
)
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _all_settings(session: Any) -> dict[str, str]:
    return {row.key: row.value for row in session.exec(select(Setting).order_by(Setting.key)).all()}


def _row(session: Any, key: str) -> Setting | None:
    return session.exec(select(Setting).where(Setting.key == key)).first()


def _ensure_row(session: Any, key: str) -> Setting:
    """Registry keys are seeded as rows at startup; recreate a missing one at its default."""
    row = _row(session, key)
    if row is None:
        row = Setting(key=key, value=to_storage(SETTINGS[key].default_value()))
        session.add(row)
        session.flush()
        session.refresh(row)
    return row


def _setting_read(row: Setting, key: str) -> SettingRead:
    default = SETTINGS[key].default_value()
    try:
        value = parse_setting(key, row.value)
    except SettingValueError:
        value = row.value
    return SettingRead(
        id=row.id or 0,
        key=key,
        value=value,
        updated_at=row.updated_at,
        default=default,
        is_default=value == default,
        pending_restart=False,
    )


def _known_key(key: str) -> str:
    if key not in SETTINGS:
        raise NotFoundError(f"unknown setting '{key}'")
    return key


@router.get("/", response_model=OpalSettingsMap, responses=OPAL_422_RESPONSE, operation_id="opal_settings_get")
def opal_settings_get(response: Response, svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    with svc.db.session() as session:
        data = _all_settings(session)
    response.headers["ETag"] = compute_etag(data)
    return data


@router.patch(
    "/",
    response_model=OpalSettingsMap,
    responses={**OPAL_409_RESPONSE, **OPAL_422_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="opal_settings_patch",
)
def opal_settings_patch(
    changes: OpalSettingsMap,
    response: Response,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> dict[str, str]:
    """Atomically patch one or more settings. Requires a strong If-Match (missing 428, stale 412)."""
    patch = changes.model_dump()
    if not patch:
        raise InvalidRequestError("at least one setting must be provided")
    with svc.config_change() as session:
        current = _all_settings(session)
        require_matching_etag(if_match, compute_etag(current), "settings")
        for key, raw_value in patch.items():
            try:
                parsed = parse_setting(key, raw_value)
            except SettingValueError as exc:
                raise InvalidRequestError(str(exc)) from exc
            row = session.exec(select(Setting).where(Setting.key == key)).first() or Setting(key=key, value="")
            row.value = to_storage(parsed)
            row.updated_at = utc_now()
            session.add(row)
        session.flush()
        result = _all_settings(session)
    response.headers["ETag"] = compute_etag(result)
    return result


@router.get(
    "/{key}",
    operation_id="opal_setting_get",
    response_model=SettingRead,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
)
def opal_setting_get(key: str = Path(min_length=1), svc: ListenerService = Depends(get_service)) -> SettingRead:
    """Read one setting with its registry default metadata (kernel opal_setting_get)."""
    with svc.db.session() as session:
        row = _ensure_row(session, _known_key(key))
        return _setting_read(row, key)


@router.put(
    "/{key}",
    operation_id="opal_setting_put",
    response_model=SettingRead,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES, **OPAL_422_RESPONSE},
)
def opal_setting_put(
    update: SettingUpdate,
    key: str = Path(min_length=1),
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> SettingRead:
    """Create or replace one setting value. Requires the settings-map If-Match (428/412)."""
    key = _known_key(key)
    try:
        parsed = parse_setting(key, update.value)
    except SettingValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    with svc.config_change() as session:
        require_matching_etag(if_match, compute_etag(_all_settings(session)), "settings")
        row = _ensure_row(session, key)
        row.value = to_storage(parsed)
        row.updated_at = utc_now()
        session.add(row)
        session.flush()
        session.refresh(row)
        read = _setting_read(row, key)
    return read


@router.delete(
    "/{key}",
    status_code=204,
    operation_id="opal_setting_delete",
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
)
def opal_setting_delete(
    key: str = Path(min_length=1),
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    """Revert one setting to its registry default. Requires the settings-map If-Match."""
    key = _known_key(key)
    with svc.config_change() as session:
        require_matching_etag(if_match, compute_etag(_all_settings(session)), "settings")
        row = _row(session, key)
        if row is not None:
            row.value = to_storage(SETTINGS[key].default_value())
            row.updated_at = utc_now()
            session.add(row)
            session.flush()
    return Response(status_code=204)
