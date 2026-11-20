from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from opal_listener.models.opal_standard import OPAL_404_RESPONSE
from opal_listener.models.schemas import (
    FailedUtteranceCreate,
    FailedUtteranceRead,
    FailedUtteranceUpdate,
    IdPath,
)
from opal_listener.models.tables import FailedUtterance
from opal_listener.service import recognition_store
from opal_listener.service.catalog import get_by_ref, parse_id
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/failed_utterance", tags=["failed_utterances"])

_LIMIT = Query(default=100, ge=1, le=500)


@router.get("/", response_model=list[FailedUtteranceRead], operation_id="list_failed_utterances")
def list_failed_utterances(
    reviewed: bool | None = None,
    limit: int = _LIMIT,
    svc: ListenerService = Depends(get_service),
) -> list[FailedUtteranceRead]:
    with svc.db.session() as session:
        rows = recognition_store.list_failed(session, reviewed, limit, 0)
        return [FailedUtteranceRead.model_validate(r) for r in rows]


@router.post(
    "/",
    response_model=FailedUtteranceRead,
    status_code=201,
    operation_id="create_failed_utterance",
)
def create_failed_utterance(
    data: FailedUtteranceCreate, response: Response, svc: ListenerService = Depends(get_service)
) -> FailedUtteranceRead:
    with svc.db.session() as session:
        obj = recognition_store.create_failed(session, data)
        session.commit()
        result = FailedUtteranceRead.model_validate(obj)
    response.headers["Location"] = f"/api/v1/failed_utterance/{result.id}"
    return result


@router.get(
    "/{failed_utterance_id}",
    response_model=FailedUtteranceRead,
    responses=OPAL_404_RESPONSE,
    operation_id="show_failed_utterance",
)
def show_failed_utterance(
    failed_utterance_id: IdPath, svc: ListenerService = Depends(get_service)
) -> FailedUtteranceRead:
    with svc.db.session() as session:
        return FailedUtteranceRead.model_validate(
            get_by_ref(session, FailedUtterance, failed_utterance_id, "failed utterance")
        )


@router.patch(
    "/{failed_utterance_id}",
    response_model=FailedUtteranceRead,
    responses=OPAL_404_RESPONSE,
    operation_id="update_failed_utterance",
)
def update_failed_utterance(
    failed_utterance_id: IdPath, data: FailedUtteranceUpdate, svc: ListenerService = Depends(get_service)
) -> FailedUtteranceRead:
    with svc.db.session() as session:
        obj = recognition_store.update_failed(session, parse_id(failed_utterance_id, "failed utterance"), data)
        session.commit()
        return FailedUtteranceRead.model_validate(obj)


@router.delete(
    "/{failed_utterance_id}",
    status_code=204,
    responses=OPAL_404_RESPONSE,
    operation_id="remove_failed_utterance",
)
def remove_failed_utterance(failed_utterance_id: IdPath, svc: ListenerService = Depends(get_service)) -> Response:
    with svc.db.session() as session:
        session.delete(get_by_ref(session, FailedUtterance, failed_utterance_id, "failed utterance"))
        session.commit()
    return Response(status_code=204)
