"""Portable settings: collection GET plus per-key GET/PUT/DELETE (OPAL kernel v2)."""

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

router = APIRouter(prefix="/api/v1/setting", tags=["config"])


def _all_settings(session: Any) -> dict[str, str]:
    return {row.key: row.value for row in session.exec(select(Setting).order_by(Setting.key)).all()}


def _map_etag(session: Any) -> str:
    """Strong revision of the whole settings map; the If-Match scope for per-key writes."""
    return compute_etag(_all_settings(session))


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


def _setting_read(svc: ListenerService, row: Setting, key: str) -> SettingRead:
    definition = SETTINGS[key]
    default = definition.default_value()
    try:
        configured = parse_setting(key, row.value)
    except SettingValueError:
        configured = row.value
    has_override = configured != default
    return SettingRead(
        id=row.id or 0,
        key=key,
        value=configured,
        configured_value=configured,
        updated_at=row.updated_at,
        default=default,
        has_override=has_override,
        is_default=not has_override,
        source="persisted" if has_override else "default",
        restart_required=definition.restart_required,
        pending_restart=svc.setting_pending_restart(key, configured),
    )


def _known_key(key: str) -> str:
    if key not in SETTINGS:
        raise NotFoundError(f"unknown setting '{key}'")
    return key


@router.get("/", response_model=OpalSettingsMap, responses=OPAL_422_RESPONSE, operation_id="opal_setting_list")
def opal_setting_list(response: Response, svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    with svc.db.session() as session:
        data = _all_settings(session)
    response.headers["ETag"] = compute_etag(data)
    return data


@router.get(
    "/{key}",
    operation_id="opal_setting_get",
    response_model=SettingRead,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
)
def opal_setting_get(
    key: str = Path(min_length=1), response: Response = ..., svc: ListenerService = Depends(get_service)
) -> SettingRead:
    """Read one setting record: effective value, configured value, default and provenance."""
    with svc.db.session() as session:
        row = _ensure_row(session, _known_key(key))
        etag = _map_etag(session)
        read = _setting_read(svc, row, key)
    response.headers["ETag"] = etag
    return read


@router.put(
    "/{key}",
    operation_id="opal_setting_put",
    response_model=SettingRead,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES, **OPAL_422_RESPONSE},
)
def opal_setting_put(
    update: SettingUpdate,
    response: Response,
    key: str = Path(min_length=1),
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SettingRead:
    """Create or replace one setting override. Requires the settings-map If-Match (428/412)."""
    key = _known_key(key)
    try:
        parsed = parse_setting(key, update.value)
    except SettingValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    with svc.config_change() as session:
        require_matching_etag(if_match, _map_etag(session), "settings")
        row = _ensure_row(session, key)
        row.value = to_storage(parsed)
        row.updated_at = utc_now()
        session.add(row)
        session.flush()
        session.refresh(row)
        read = _setting_read(svc, row, key)
        etag = _map_etag(session)
    response.headers["ETag"] = etag
    return read


@router.delete(
    "/{key}",
    status_code=204,
    operation_id="opal_setting_delete",
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES, **OPAL_422_RESPONSE},
)
def opal_setting_delete(
    response: Response,
    key: str = Path(min_length=1),
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    """Delete the persistent override (revert to default). 204 even with no override; If-Match required."""
    key = _known_key(key)
    with svc.config_change() as session:
        require_matching_etag(if_match, _map_etag(session), "settings")
        row = _row(session, key)
        if row is not None:
            row.value = to_storage(SETTINGS[key].default_value())
            row.updated_at = utc_now()
            session.add(row)
            session.flush()
        etag = _map_etag(session)
    return Response(status_code=204, headers={"ETag": etag})
