"""Atomic portable settings: GET and PATCH /api/v1/settings/ exclusively (OPAL v1.2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import select

from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.tables import Setting, utc_now
from opal_listener.service.concurrency import compute_etag, require_matching_etag
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.settings_registry import SettingValueError, parse_setting, to_storage
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _all_settings(session: Any) -> dict[str, str]:
    return {row.key: row.value for row in session.exec(select(Setting).order_by(Setting.key)).all()}


@router.get("/", responses=OPAL_422_RESPONSE)
def get_all_settings(response: Response, svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    with svc.db.session() as session:
        data = _all_settings(session)
    response.headers["ETag"] = compute_etag(data)
    return data


@router.patch("/", operation_id="patch_listen_settings", responses=OPAL_PRECONDITION_RESPONSES)
def patch_listen_settings(
    changes: dict[str, Any],
    response: Response,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> dict[str, str]:
    """Atomically patch one or more settings. Requires a strong If-Match (missing 428, stale 412)."""
    if not changes:
        raise InvalidRequestError("at least one setting must be provided")
    with svc.config_change() as session:
        current = _all_settings(session)
        require_matching_etag(if_match, compute_etag(current), "settings")
        for key, raw_value in changes.items():
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
