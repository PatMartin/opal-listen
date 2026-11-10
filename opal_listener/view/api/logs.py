from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from opal_listener.models.opal_standard import OPAL_404_RESPONSE, OPAL_422_RESPONSE
from opal_listener.models.schemas import RecognitionLogRead
from opal_listener.models.tables import RecognitionLog
from opal_listener.service import recognition_store
from opal_listener.service.catalog import get_or_404
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/log", tags=["logs"])

_SCOPE = "logs"
_CURSOR_DESC = "Opaque continuation token returned from the preceding page."
_LIMIT_DESC = "Maximum items per page."


@router.get("/", response_model=list[RecognitionLogRead], operation_id="list_logs_api_v1_logs__get")
def list_logs(
    limit: int = Query(default=100, ge=1, le=500, description=_LIMIT_DESC),
    intent_name: str | None = Query(default=None),
    matched: bool | None = Query(default=None),
    svc: ListenerService = Depends(get_service),
) -> list[RecognitionLogRead]:
    """Bounded list, stable descending time-plus-ID ordering; `limit` caps the returned items."""
    with svc.db.session() as session:
        rows = recognition_store.list_logs(session, limit, intent_name, matched)
        return [RecognitionLogRead.model_validate(r) for r in rows]


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
