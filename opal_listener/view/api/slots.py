from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.schemas import (
    SlotAliasCreate,
    SlotAliasRead,
    SlotCreate,
    SlotRead,
    SlotReadWithValues,
    SlotUpdate,
    SlotValueCreate,
    SlotValueRead,
    SlotValueReadWithAliases,
)
from opal_listener.models.tables import Slot
from opal_listener.service import catalog
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/slots", tags=["slots"])


@router.get("/", response_model=list[SlotReadWithValues], responses=OPAL_422_RESPONSE)
def list_slots(response: Response, svc: ListenerService = Depends(get_service)) -> list[SlotReadWithValues]:
    with svc.db.session() as session:
        items = [SlotReadWithValues.model_validate(x) for x in catalog.list_slots(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/", response_model=SlotRead, status_code=201)
def create_slot(data: SlotCreate, response: Response, svc: ListenerService = Depends(get_service)) -> SlotRead:
    with svc.config_change() as session:
        result = SlotRead.model_validate(catalog.create_slot(session, data))
    response.headers["Location"] = f"/api/v1/slots/{result.id}"
    return result


@router.get("/{slot_id}", response_model=SlotReadWithValues)
def get_slot(slot_id: int, response: Response, svc: ListenerService = Depends(get_service)) -> SlotReadWithValues:
    with svc.db.session() as session:
        item = SlotReadWithValues.model_validate(catalog.get_or_404(session, Slot, slot_id, "slot"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch("/{slot_id}", response_model=SlotRead, responses=OPAL_PRECONDITION_RESPONSES)
def update_slot(
    slot_id: int,
    data: SlotUpdate,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> SlotRead:
    with svc.config_change() as session:
        current = SlotReadWithValues.model_validate(catalog.get_or_404(session, Slot, slot_id, "slot"))
        require_matching_etag(if_match, item_etag(current), "slot")
        result = SlotRead.model_validate(catalog.update_slot(session, slot_id, data))
    return result


@router.delete("/{slot_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES)
def delete_slot(
    slot_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = SlotReadWithValues.model_validate(catalog.get_or_404(session, Slot, slot_id, "slot"))
        require_matching_etag(if_match, item_etag(current), "slot")
        catalog.delete_slot(session, slot_id)
    return Response(status_code=204)


@router.get("/{slot_id}/values", response_model=list[SlotValueReadWithAliases])
def list_slot_values(
    slot_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> list[SlotValueReadWithAliases]:
    with svc.db.session() as session:
        slot = catalog.get_or_404(session, Slot, slot_id, "slot")
        items = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/{slot_id}/values", response_model=SlotValueRead, status_code=201)
def add_slot_value(
    slot_id: int, data: SlotValueCreate, response: Response, svc: ListenerService = Depends(get_service)
) -> SlotValueRead:
    with svc.config_change() as session:
        result = SlotValueRead.model_validate(catalog.add_slot_value(session, slot_id, data))
    response.headers["Location"] = f"/api/v1/slots/{slot_id}/values/{result.id}"
    return result


@router.delete("/{slot_id}/values/{value_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES)
def delete_slot_value(
    slot_id: int,
    value_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        slot = catalog.get_or_404(session, Slot, slot_id, "slot")
        current_values = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
        require_matching_etag(if_match, collection_etag(current_values), "slot values")
        catalog.delete_slot_value(session, slot_id, value_id)
    return Response(status_code=204)


@router.get("/{slot_id}/values/{value_id}/aliases", response_model=list[SlotAliasRead])
def list_aliases(
    slot_id: int, value_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> list[SlotAliasRead]:
    with svc.db.session() as session:
        value = catalog.get_slot_value(session, slot_id, value_id)
        items = [SlotAliasRead.model_validate(a) for a in value.aliases]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/{slot_id}/values/{value_id}/aliases", response_model=SlotAliasRead, status_code=201)
def add_alias(
    slot_id: int,
    value_id: int,
    data: SlotAliasCreate,
    response: Response,
    svc: ListenerService = Depends(get_service),
) -> SlotAliasRead:
    with svc.config_change() as session:
        result = SlotAliasRead.model_validate(catalog.add_alias(session, slot_id, value_id, data))
    response.headers["Location"] = f"/api/v1/slots/{slot_id}/values/{value_id}/aliases/{result.id}"
    return result


@router.delete(
    "/{slot_id}/values/{value_id}/aliases/{alias_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES
)
def delete_alias(
    slot_id: int,
    value_id: int,
    alias_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        value = catalog.get_slot_value(session, slot_id, value_id)
        current_aliases = [SlotAliasRead.model_validate(a) for a in value.aliases]
        require_matching_etag(if_match, collection_etag(current_aliases), "aliases")
        catalog.delete_alias(session, slot_id, value_id, alias_id)
    return Response(status_code=204)
