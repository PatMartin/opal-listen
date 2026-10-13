from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from opal_listener.models.schemas import (
    FailedUtteranceCreate,
    FailedUtteranceRead,
    FailedUtteranceUpdate,
    IntentPhraseRead,
    PromoteResult,
    RegressionTestRead,
)
from opal_listener.models.tables import FailedUtterance
from opal_listener.service import recognition_store, review
from opal_listener.service.catalog import get_or_404
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/failed_utterances", tags=["failed_utterances"])


@router.get("/", response_model=list[FailedUtteranceRead])
def list_failed_utterances(
    reviewed: bool | None = None,
    promoted: bool | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    svc: ListenerService = Depends(get_service),
) -> list[FailedUtteranceRead]:
    with svc.db.session() as session:
        rows = recognition_store.list_failed(session, reviewed, promoted, limit, offset)
        return [FailedUtteranceRead.model_validate(r) for r in rows]


@router.post("/", response_model=FailedUtteranceRead, status_code=201)
def create_failed_utterance(
    data: FailedUtteranceCreate, response: Response, svc: ListenerService = Depends(get_service)
) -> FailedUtteranceRead:
    with svc.db.session() as session:
        obj = recognition_store.create_failed(session, data)
        session.commit()
        result = FailedUtteranceRead.model_validate(obj)
    response.headers["Location"] = f"/api/v1/failed_utterances/{result.id}"
    return result


@router.get("/{fu_id}", response_model=FailedUtteranceRead)
def get_failed_utterance(fu_id: int, svc: ListenerService = Depends(get_service)) -> FailedUtteranceRead:
    with svc.db.session() as session:
        return FailedUtteranceRead.model_validate(get_or_404(session, FailedUtterance, fu_id, "failed utterance"))


@router.patch("/{fu_id}", response_model=FailedUtteranceRead)
def update_failed_utterance(
    fu_id: int, data: FailedUtteranceUpdate, svc: ListenerService = Depends(get_service)
) -> FailedUtteranceRead:
    with svc.db.session() as session:
        obj = recognition_store.update_failed(session, fu_id, data)
        session.commit()
        return FailedUtteranceRead.model_validate(obj)


@router.delete("/{fu_id}", status_code=204)
def delete_failed_utterance(fu_id: int, svc: ListenerService = Depends(get_service)) -> Response:
    with svc.db.session() as session:
        session.delete(get_or_404(session, FailedUtterance, fu_id, "failed utterance"))
        session.commit()
    return Response(status_code=204)


@router.post("/{fu_id}/promote", response_model=PromoteResult)
def promote_to_phrase(
    fu_id: int,
    intent_name: str,
    create_regression: bool = Query(default=True, description="Also add a regression test for this utterance"),
    svc: ListenerService = Depends(get_service),
) -> PromoteResult:
    """Promote a failed utterance as a training phrase for the given intent."""
    with svc.config_change() as session:
        fu, phrase, regression = review.promote(session, svc, fu_id, intent_name, create_regression)
        session.flush()
        result = PromoteResult(
            failed_utterance=FailedUtteranceRead.model_validate(fu),
            phrase=IntentPhraseRead.model_validate(phrase),
            regression_test=RegressionTestRead.model_validate(regression) if regression else None,
        )
    return result
