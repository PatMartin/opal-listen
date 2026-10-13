from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.schemas import (
    IntentCreate,
    IntentPhraseCreate,
    IntentPhraseRead,
    IntentRead,
    IntentReadWithPhrases,
    IntentUpdate,
)
from opal_listener.models.tables import Intent
from opal_listener.service import catalog
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/intents", tags=["intents"])


@router.get("/", response_model=list[IntentReadWithPhrases], responses=OPAL_422_RESPONSE)
def list_intents(response: Response, svc: ListenerService = Depends(get_service)) -> list[IntentReadWithPhrases]:
    with svc.db.session() as session:
        items = [IntentReadWithPhrases.model_validate(i) for i in catalog.list_intents(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/", response_model=IntentReadWithPhrases, status_code=201)
def create_intent(
    data: IntentCreate, response: Response, svc: ListenerService = Depends(get_service)
) -> IntentReadWithPhrases:
    with svc.config_change() as session:
        result = IntentReadWithPhrases.model_validate(catalog.create_intent(session, data))
    response.headers["Location"] = f"/api/v1/intents/{result.id}"
    return result


@router.get("/{intent_id}", response_model=IntentReadWithPhrases)
def get_intent(
    intent_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> IntentReadWithPhrases:
    with svc.db.session() as session:
        item = IntentReadWithPhrases.model_validate(catalog.get_or_404(session, Intent, intent_id, "intent"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch("/{intent_id}", response_model=IntentRead, responses=OPAL_PRECONDITION_RESPONSES)
def update_intent(
    intent_id: int,
    data: IntentUpdate,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> IntentRead:
    with svc.config_change() as session:
        current = IntentReadWithPhrases.model_validate(catalog.get_or_404(session, Intent, intent_id, "intent"))
        require_matching_etag(if_match, item_etag(current), "intent")
        result = IntentRead.model_validate(catalog.update_intent(session, intent_id, data))
    return result


@router.delete("/{intent_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES)
def delete_intent(
    intent_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = IntentReadWithPhrases.model_validate(catalog.get_or_404(session, Intent, intent_id, "intent"))
        require_matching_etag(if_match, item_etag(current), "intent")
        catalog.delete_intent(session, intent_id)
    return Response(status_code=204)


@router.get("/{intent_id}/phrases", response_model=list[IntentPhraseRead])
def list_phrases(
    intent_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> list[IntentPhraseRead]:
    with svc.db.session() as session:
        intent = catalog.get_or_404(session, Intent, intent_id, "intent")
        items = [IntentPhraseRead.model_validate(p) for p in intent.phrases]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/{intent_id}/phrases", response_model=IntentPhraseRead, status_code=201)
def add_phrase(
    intent_id: int, data: IntentPhraseCreate, response: Response, svc: ListenerService = Depends(get_service)
) -> IntentPhraseRead:
    with svc.config_change() as session:
        result = IntentPhraseRead.model_validate(catalog.add_phrase(session, intent_id, data))
    response.headers["Location"] = f"/api/v1/intents/{intent_id}/phrases/{result.id}"
    return result


@router.delete("/{intent_id}/phrases/{phrase_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES)
def delete_phrase(
    intent_id: int,
    phrase_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        intent = catalog.get_or_404(session, Intent, intent_id, "intent")
        current_phrases = [IntentPhraseRead.model_validate(p) for p in intent.phrases]
        require_matching_etag(if_match, collection_etag(current_phrases), "phrases")
        catalog.delete_phrase(session, intent_id, phrase_id)
    return Response(status_code=204)
