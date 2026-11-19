from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Response

from opal_listener.models.opal_standard import OPAL_404_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.schemas import (
    IdPath,
    FailedUtteranceCreate,
    FailedUtterancePromoteRequest,
    FailedUtteranceRead,
    FailedUtteranceUpdate,
    IntentPhraseRead,
    PromoteResult,
    RegressionTestRead,
)
from opal_listener.models.tables import FailedUtterance, Intent
from opal_listener.service import recognition_store, review
from opal_listener.service.catalog import get_by_ref, get_or_404, parse_id
from opal_listener.service.concurrency import collection_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/failed_utterance", tags=["failed_utterances"])

_LIMIT = Query(default=100, ge=1, le=500)


@router.get("/", response_model=list[FailedUtteranceRead], operation_id="list_failed_utterances")
def list_failed_utterances(
    reviewed: bool | None = None,
    promoted: bool | None = None,
    limit: int = _LIMIT,
    svc: ListenerService = Depends(get_service),
) -> list[FailedUtteranceRead]:
    with svc.db.session() as session:
        rows = recognition_store.list_failed(session, reviewed, promoted, limit, 0)
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
        obj = recognition_store.update_failed(
            session, parse_id(failed_utterance_id, "failed utterance"), data
        )
        session.commit()
        return FailedUtteranceRead.model_validate(obj)


@router.delete(
    "/{failed_utterance_id}",
    status_code=204,
    responses=OPAL_404_RESPONSE,
    operation_id="remove_failed_utterance",
)
def remove_failed_utterance(
    failed_utterance_id: IdPath, svc: ListenerService = Depends(get_service)
) -> Response:
    with svc.db.session() as session:
        session.delete(get_by_ref(session, FailedUtterance, failed_utterance_id, "failed utterance"))
        session.commit()
    return Response(status_code=204)


@router.post(
    "/{failed_utterance_id}/promote",
    response_model=PromoteResult,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="promote_failed_utterance",
)
def promote_failed_utterance(
    data: FailedUtterancePromoteRequest,
    failed_utterance_id: IdPath,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> PromoteResult:
    """Promote a failed utterance as a training phrase for the given intent id."""
    with svc.config_change() as session:
        intent_id = parse_id(data.intent_id, "intent")
        intent = get_or_404(session, Intent, intent_id, "intent")
        phrases = [IntentPhraseRead.model_validate(p) for p in intent.phrases]
        require_matching_etag(if_match, collection_etag(phrases), "phrases")
        fu, phrase, regression = review.promote(
            session, svc, parse_id(failed_utterance_id, "failed utterance"), intent, data.create_regression
        )
        session.flush()
        result = PromoteResult(
            failed_utterance=FailedUtteranceRead.model_validate(fu),
            phrase=IntentPhraseRead.model_validate(phrase),
            regression_test=RegressionTestRead.model_validate(regression) if regression else None,
        )
    return result
