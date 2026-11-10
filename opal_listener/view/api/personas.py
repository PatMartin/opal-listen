"""OPAL persona registry: named speaker identities optionally bound to an OPAL Say voice_id."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Path, Response

from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
)
from opal_listener.models.schemas import (
    PERSONA_ID_PATTERN,
    OpalValidationReport,
    OpalPersonaCreate,
    OpalPersonaRead,
    OpalPersonaUpdate,
)
from opal_listener.service import catalog
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/persona", tags=["persona"])

_ITEM_ERRORS = {**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_422_RESPONSE, **OPAL_PRECONDITION_RESPONSES}


@router.get("/", response_model=list[OpalPersonaRead], responses=OPAL_422_RESPONSE, operation_id="opal_persona_list")
def opal_persona_list(
    response: Response, enabled: bool | None = None, svc: ListenerService = Depends(get_service)
) -> list[OpalPersonaRead]:
    """List registered personas; optionally filter by enabled state."""
    with svc.db.session() as session:
        items = [OpalPersonaRead.model_validate(p) for p in catalog.list_personas(session, enabled)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/", response_model=OpalPersonaRead, status_code=201, responses=_ITEM_ERRORS, operation_id="opal_persona_create"
)
def opal_persona_create(
    data: OpalPersonaCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> OpalPersonaRead:
    """Register a persona. `voice_id` names an OPAL Say voice; existence is not validated locally."""
    with svc.config_change() as session:
        current = [OpalPersonaRead.model_validate(p) for p in catalog.list_personas(session)]
        require_matching_etag(if_match, collection_etag(current), "personas")
        result = OpalPersonaRead.model_validate(catalog.create_persona(session, data))
    response.headers["Location"] = f"/api/v1/persona/{result.persona_id}"
    return result


@router.post(
    "/{persona_id}/validate",
    response_model=OpalValidationReport,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_persona_validate",
)
def opal_persona_validate(
    persona_id: str = Path(min_length=1, max_length=200, pattern=PERSONA_ID_PATTERN),
    svc: ListenerService = Depends(get_service),
) -> OpalValidationReport:
    """Check that a persona is registered and enabled and its declared bindings are well-formed."""
    with svc.db.session() as session:
        persona = catalog.get_persona(session, persona_id)
        return catalog.validate_persona(persona)


@router.get(
    "/{persona_id}",
    response_model=OpalPersonaRead,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_persona_get",
)
def opal_persona_get(
    response: Response,
    persona_id: str = Path(min_length=1, max_length=200, pattern=PERSONA_ID_PATTERN),
    svc: ListenerService = Depends(get_service),
) -> OpalPersonaRead:
    with svc.db.session() as session:
        item = OpalPersonaRead.model_validate(catalog.get_persona(session, persona_id))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{persona_id}",
    response_model=OpalPersonaRead,
    responses=_ITEM_ERRORS,
    operation_id="opal_persona_patch",
)
def opal_persona_patch(
    data: OpalPersonaUpdate,
    persona_id: str = Path(min_length=1, max_length=200, pattern=PERSONA_ID_PATTERN),
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> OpalPersonaRead:
    with svc.config_change() as session:
        current = OpalPersonaRead.model_validate(catalog.get_persona(session, persona_id))
        require_matching_etag(if_match, item_etag(current), "persona")
        result = OpalPersonaRead.model_validate(catalog.update_persona(session, persona_id, data))
    return result


@router.delete("/{persona_id}", status_code=204, responses=_ITEM_ERRORS, operation_id="opal_persona_delete")
def opal_persona_delete(
    persona_id: str = Path(min_length=1, max_length=200, pattern=PERSONA_ID_PATTERN),
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = OpalPersonaRead.model_validate(catalog.get_persona(session, persona_id))
        require_matching_etag(if_match, item_etag(current), "persona")
        catalog.delete_persona(session, persona_id)
    return Response(status_code=204)
