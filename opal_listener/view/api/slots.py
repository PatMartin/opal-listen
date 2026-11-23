from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
)
from opal_listener.models.schemas import (
    IdPath,
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
    operation_id="list_slots",
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
    operation_id="create_slot",
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
    operation_id="show_slot",
)
def show_slot(slot_id: IdPath, response: Response, svc: ListenerService = Depends(get_service)) -> SlotReadWithValues:
    with svc.db.session() as session:
        item = SlotReadWithValues.model_validate(catalog.get_by_ref(session, Slot, slot_id, "slot"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{slot_id}",
    response_model=SlotRead,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="update_slot",
)
def update_slot(
    slot_id: IdPath,
    data: SlotUpdate,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotRead:
    with svc.config_change() as session:
        current = SlotReadWithValues.model_validate(catalog.get_by_ref(session, Slot, slot_id, "slot"))
        require_matching_etag(if_match, item_etag(current), "slot")
        result = SlotRead.model_validate(catalog.update_slot(session, catalog.parse_id(slot_id, "slot"), data))
    return result


@router.delete(
    "/{slot_id}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="remove_slot",
)
def remove_slot(
    slot_id: IdPath,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = SlotReadWithValues.model_validate(catalog.get_by_ref(session, Slot, slot_id, "slot"))
        require_matching_etag(if_match, item_etag(current), "slot")
        catalog.delete_slot(session, catalog.parse_id(slot_id, "slot"))
    return Response(status_code=204)


@router.get(
    "/{slot_id}/value",
    response_model=list[SlotValueReadWithAliases],
    responses=OPAL_404_RESPONSE,
    operation_id="list_slot_values",
)
def list_slot_values(
    slot_id: IdPath, response: Response, svc: ListenerService = Depends(get_service)
) -> list[SlotValueReadWithAliases]:
    with svc.db.session() as session:
        slot = catalog.get_by_ref(session, Slot, slot_id, "slot")
        items = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/{slot_id}/value",
    response_model=SlotValueRead,
    status_code=201,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="add_slot_value",
)
def add_slot_value(
    slot_id: IdPath,
    data: SlotValueCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotValueRead:
    with svc.config_change() as session:
        slot = catalog.get_by_ref(session, Slot, slot_id, "slot")
        current_values = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
        require_matching_etag(if_match, collection_etag(current_values), "slot values")
        result = SlotValueRead.model_validate(catalog.add_slot_value(session, catalog.parse_id(slot_id, "slot"), data))
    response.headers["Location"] = f"/api/v1/slot/{slot_id}/value/{result.id}"
    return result


@router.get(
    "/{slot_id}/value/{value_id}",
    response_model=SlotValueReadWithAliases,
    responses=OPAL_404_RESPONSE,
    operation_id="show_slot_value",
)
def show_slot_value(
    slot_id: IdPath, value_id: IdPath, response: Response, svc: ListenerService = Depends(get_service)
) -> SlotValueReadWithAliases:
    with svc.db.session() as session:
        value = catalog.get_slot_value(
            session, catalog.parse_id(slot_id, "slot"), catalog.parse_id(value_id, "slot value")
        )
        item = SlotValueReadWithAliases.model_validate(value)
    response.headers["ETag"] = item_etag(item)
    return item


@router.delete(
    "/{slot_id}/value/{value_id}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="remove_slot_value",
)
def remove_slot_value(
    slot_id: IdPath,
    value_id: IdPath,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        slot = catalog.get_by_ref(session, Slot, slot_id, "slot")
        current_values = [SlotValueReadWithAliases.model_validate(v) for v in slot.values]
        require_matching_etag(if_match, collection_etag(current_values), "slot values")
        catalog.delete_slot_value(session, catalog.parse_id(slot_id, "slot"), catalog.parse_id(value_id, "slot value"))
    return Response(status_code=204)


@router.get(
    "/{slot_id}/value/{value_id}/alias",
    response_model=list[SlotAliasRead],
    responses=OPAL_404_RESPONSE,
    operation_id="list_slot_aliases",
)
def list_slot_aliases(
    slot_id: IdPath, value_id: IdPath, response: Response, svc: ListenerService = Depends(get_service)
) -> list[SlotAliasRead]:
    with svc.db.session() as session:
        value = catalog.get_slot_value(
            session, catalog.parse_id(slot_id, "slot"), catalog.parse_id(value_id, "slot value")
        )
        items = [SlotAliasRead.model_validate(a) for a in value.aliases]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/{slot_id}/value/{value_id}/alias",
    response_model=SlotAliasRead,
    status_code=201,
    responses={**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="add_slot_alias",
)
def add_slot_alias(
    slot_id: IdPath,
    value_id: IdPath,
    data: SlotAliasCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> SlotAliasRead:
    with svc.config_change() as session:
        value = catalog.get_slot_value(
            session, catalog.parse_id(slot_id, "slot"), catalog.parse_id(value_id, "slot value")
        )
        current_aliases = [SlotAliasRead.model_validate(a) for a in value.aliases]
        require_matching_etag(if_match, collection_etag(current_aliases), "aliases")
        result = SlotAliasRead.model_validate(
            catalog.add_alias(
                session, catalog.parse_id(slot_id, "slot"), catalog.parse_id(value_id, "slot value"), data
            )
        )
    response.headers["Location"] = f"/api/v1/slot/{slot_id}/value/{value_id}/alias/{result.id}"
    return result


@router.get(
    "/{slot_id}/value/{value_id}/alias/{alias_id}",
    response_model=SlotAliasRead,
    responses=OPAL_404_RESPONSE,
    operation_id="show_slot_alias",
)
def show_slot_alias(
    slot_id: IdPath,
    value_id: IdPath,
    alias_id: IdPath,
    response: Response,
    svc: ListenerService = Depends(get_service),
) -> SlotAliasRead:
    with svc.db.session() as session:
        item = SlotAliasRead.model_validate(
            catalog.get_alias(
                session,
                catalog.parse_id(slot_id, "slot"),
                catalog.parse_id(value_id, "slot value"),
                catalog.parse_id(alias_id, "alias"),
            )
        )
    response.headers["ETag"] = item_etag(item)
    return item


@router.delete(
    "/{slot_id}/value/{value_id}/alias/{alias_id}",
    status_code=204,
    responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES},
    operation_id="remove_slot_alias",
)
def remove_slot_alias(
    slot_id: IdPath,
    value_id: IdPath,
    alias_id: IdPath,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        value = catalog.get_slot_value(
            session, catalog.parse_id(slot_id, "slot"), catalog.parse_id(value_id, "slot value")
        )
        current_aliases = [SlotAliasRead.model_validate(a) for a in value.aliases]
        require_matching_etag(if_match, collection_etag(current_aliases), "aliases")
        catalog.delete_alias(
            session,
            catalog.parse_id(slot_id, "slot"),
            catalog.parse_id(value_id, "slot value"),
            catalog.parse_id(alias_id, "alias"),
        )
    return Response(status_code=204)
