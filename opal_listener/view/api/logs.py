from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from opal_listener.models.opal_standard import OPAL_404_RESPONSE, OPAL_422_RESPONSE
from opal_listener.models.schemas import LogPage, RecognitionLogRead
from opal_listener.models.tables import RecognitionLog
from opal_listener.service import recognition_store
from opal_listener.service.catalog import get_or_404
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.pagination import decode_cursor, encode_cursor
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/log", tags=["logs"])

_SCOPE = "logs"
_CURSOR_DESC = "Opaque continuation token returned from the preceding page."
_LIMIT_DESC = "Maximum items per page."


@router.get("/", response_model=LogPage, operation_id="list_logs_api_v1_logs__get")
def list_logs(
    limit: int = Query(default=100, ge=1, le=500, description=_LIMIT_DESC),
    cursor: str = Query(default=None, min_length=1, description=_CURSOR_DESC),
    intent_name: str | None = None,
    matched: bool | None = None,
    svc: ListenerService = Depends(get_service),
) -> LogPage:
    """Use cursor pagination, stable descending time-plus-ID ordering; filters are bound into the cursor."""
    filters = {"intent_name": intent_name, "matched": matched}
    cursor_tuple = decode_cursor(cursor, _SCOPE, filters, InvalidRequestError) if cursor is not None else None
    with svc.db.session() as session:
        rows, has_more = recognition_store.list_logs_page(session, limit, cursor_tuple, intent_name, matched)
        items = [RecognitionLogRead.model_validate(r) for r in rows]
    next_cursor = encode_cursor(_SCOPE, filters, rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return LogPage(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/count", responses=OPAL_422_RESPONSE, operation_id="count_logs_api_v1_logs_count_get")
def count_logs(
    svc: ListenerService = Depends(get_service),
) -> dict[str, int]:
    with svc.db.session() as session:
        return recognition_store.count_logs(session)


@router.get(
    "/{log_id}",
    response_model=RecognitionLogRead,
    responses=OPAL_404_RESPONSE,
    operation_id="get_log_api_v1_logs__log_id__get",
)
def get_log(log_id: int, svc: ListenerService = Depends(get_service)) -> RecognitionLogRead:
    with svc.db.session() as session:
        return RecognitionLogRead.model_validate(get_or_404(session, RecognitionLog, log_id, "recognition log"))
