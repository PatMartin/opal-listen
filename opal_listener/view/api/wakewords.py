from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.schemas import WakewordCreate, WakewordRead, WakewordUpdate
from opal_listener.models.tables import Wakeword
from opal_listener.service import catalog
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/wakewords", tags=["wakewords"])


@router.get("/", response_model=list[WakewordRead], responses=OPAL_422_RESPONSE)
def list_wakewords(response: Response, svc: ListenerService = Depends(get_service)) -> list[WakewordRead]:
    with svc.db.session() as session:
        items = [WakewordRead.model_validate(w) for w in catalog.list_wakewords(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/", response_model=WakewordRead, status_code=201)
def create_wakeword(
    data: WakewordCreate, response: Response, svc: ListenerService = Depends(get_service)
) -> WakewordRead:
    with svc.config_change() as session:
        result = WakewordRead.model_validate(catalog.create_wakeword(session, data))
    response.headers["Location"] = f"/api/v1/wakewords/{result.id}"
    return result


@router.get("/{wakeword_id}", response_model=WakewordRead)
def get_wakeword(wakeword_id: int, response: Response, svc: ListenerService = Depends(get_service)) -> WakewordRead:
    with svc.db.session() as session:
        item = WakewordRead.model_validate(catalog.get_or_404(session, Wakeword, wakeword_id, "wakeword"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch("/{wakeword_id}", response_model=WakewordRead, responses=OPAL_PRECONDITION_RESPONSES)
def update_wakeword(
    wakeword_id: int,
    data: WakewordUpdate,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> WakewordRead:
    with svc.config_change() as session:
        current = WakewordRead.model_validate(catalog.get_or_404(session, Wakeword, wakeword_id, "wakeword"))
        require_matching_etag(if_match, item_etag(current), "wakeword")
        result = WakewordRead.model_validate(catalog.update_wakeword(session, wakeword_id, data))
    return result


@router.delete("/{wakeword_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES)
def delete_wakeword(
    wakeword_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = WakewordRead.model_validate(catalog.get_or_404(session, Wakeword, wakeword_id, "wakeword"))
        require_matching_etag(if_match, item_etag(current), "wakeword")
        catalog.delete_wakeword(session, wakeword_id)
    return Response(status_code=204)
