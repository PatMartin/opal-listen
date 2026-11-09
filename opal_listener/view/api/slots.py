from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
)
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

router = APIRouter(prefix="/api/v1/slot", tags=["slots"])


@router.get(
    "/",
    response_model=list[SlotReadWithValues],
    responses=OPAL_422_RESPONSE,
    operation_id="list_slots_api_v1_slots__get",
)
def list_slots(response: Response, svc: ListenerService = Depends(get_service)) -> list[SlotReadWithValues]:
    with svc.db.session() as session:
        items = [SlotReadWithValues.model_validate(x) for x in catalog.list_slots(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/",
    response_model=SlotRead,
    status_code=201,
    responses={**OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="create_slot_api_v1_slots__post",
)
def create_slot(
    data: SlotCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotRead:
    with svc.config_change() as session:
        current = [SlotReadWithValues.model_validate(x) for x in catalog.list_slots(session)]
        require_matching_etag(if_match, collection_etag(current), "slots")
        result = SlotRead.model_validate(catalog.create_slot(session, data))
    response.headers["Location"] = f"/api/v1/slot/{result.id}"
    return result


@router.get(
    "/{slot_id}",
    response_model=SlotReadWithValues,
    responses=OPAL_404_RESPONSE,
    operation_id="get_slot_api_v1_slots__slot_id__get",
)
def get_slot(slot_id: int, response: Response, svc: ListenerService = Depends(get_service)) -> SlotReadWithValues:
    with svc.db.session() as session:
        item = SlotReadWithValues.model_validate(catalog.get_or_404(session, Slot, slot_id, "slot"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{slot_id}",
    response_model=SlotRead,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="update_slot_api_v1_slots__slot_id__patch",
)
def update_slot(
    slot_id: int,
    data: SlotUpdate,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotRead:
    with svc.config_change() as session:
        current = SlotReadWithValues.model_validate(catalog.get_or_404(session, Slot, slot_id, "slot"))
        require_matching_etag(if_match, item_etag(current), "slot")
        result = SlotRead.model_validate(catalog.update_slot(session, slot_id, data))
    return result


@router.delete(
    "/{slot_id}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="delete_slot_api_v1_slots__slot_id__delete",
)
def delete_slot(
    slot_id: int,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = SlotReadWithValues.model_validate(catalog.get_or_404(session, Slot, slot_id, "slot"))
        require_matching_etag(if_match, item_etag(current), "slot")
        catalog.delete_slot(session, slot_id)
    return Response(status_code=204)


@router.get(
    "/{slot_id}/value",
    response_model=list[SlotValueReadWithAliases],
    responses=OPAL_404_RESPONSE,
    operation_id="list_slot_values_api_v1_slots__slot_id__values_get",
)
def list_slot_values(
    slot_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> list[SlotValueReadWithAliases]:
    with svc.db.session() as session:
        slot = catalog.get_or_404(session, Slot, slot_id, "slot")
        items = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/{slot_id}/value",
    response_model=SlotValueRead,
    status_code=201,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="add_slot_value_api_v1_slots__slot_id__values_post",
)
def add_slot_value(
    slot_id: int,
    data: SlotValueCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotValueRead:
    with svc.config_change() as session:
        slot = catalog.get_or_404(session, Slot, slot_id, "slot")
        current_values = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
        require_matching_etag(if_match, collection_etag(current_values), "slot values")
        result = SlotValueRead.model_validate(catalog.add_slot_value(session, slot_id, data))
    response.headers["Location"] = f"/api/v1/slot/{slot_id}/value/{result.id}"
    return result


@router.delete(
    "/{slot_id}/value/{value_id}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="delete_slot_value_api_v1_slots__slot_id__values__value_id__delete",
)
def delete_slot_value(
    slot_id: int,
    value_id: int,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        slot = catalog.get_or_404(session, Slot, slot_id, "slot")
        current_values = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
        require_matching_etag(if_match, collection_etag(current_values), "slot values")
        catalog.delete_slot_value(session, slot_id, value_id)
    return Response(status_code=204)


@router.get(
    "/{slot_id}/value/{value_id}/alias",
    response_model=list[SlotAliasRead],
    responses=OPAL_404_RESPONSE,
    operation_id="list_aliases_api_v1_slots__slot_id__values__value_id__aliases_get",
)
def list_aliases(
    slot_id: int, value_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> list[SlotAliasRead]:
    with svc.db.session() as session:
        value = catalog.get_slot_value(session, slot_id, value_id)
        items = [SlotAliasRead.model_validate(a) for a in value.aliases]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/{slot_id}/value/{value_id}/alias",
    response_model=SlotAliasRead,
    status_code=201,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="add_alias_api_v1_slots__slot_id__values__value_id__aliases_post",
)
def add_alias(
    slot_id: int,
    value_id: int,
    data: SlotAliasCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotAliasRead:
    with svc.config_change() as session:
        value = catalog.get_slot_value(session, slot_id, value_id)
        current_aliases = [SlotAliasRead.model_validate(a) for a in value.aliases]
        require_matching_etag(if_match, collection_etag(current_aliases), "aliases")
        result = SlotAliasRead.model_validate(catalog.add_alias(session, slot_id, value_id, data))
    response.headers["Location"] = f"/api/v1/slot/{slot_id}/value/{value_id}/alias/{result.id}"
    return result


@router.delete(
    "/{slot_id}/value/{value_id}/alias/{alias_id}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="delete_alias_api_v1_slots__slot_id__values__value_id__aliases__alias_id__delete",
)
def delete_alias(
    slot_id: int,
    value_id: int,
    alias_id: int,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        value = catalog.get_slot_value(session, slot_id, value_id)
        current_aliases = [SlotAliasRead.model_validate(a) for a in value.aliases]
        require_matching_etag(if_match, collection_etag(current_aliases), "aliases")
        catalog.delete_alias(session, slot_id, value_id, alias_id)
    return Response(status_code=204)
