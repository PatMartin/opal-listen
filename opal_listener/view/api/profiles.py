from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Path, Response

from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_409_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_PRECONDITION_RESPONSES,
)
from opal_listener.models.schemas import OpalProfile, OpalProfileCreate, OpalProfilePatch
from opal_listener.models.tables import Profile
from opal_listener.service import catalog
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])

_ITEM_ERRORS = {**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_422_RESPONSE, **OPAL_PRECONDITION_RESPONSES}


@router.get("/", response_model=list[OpalProfile], responses=OPAL_422_RESPONSE, operation_id="opal_profiles_list")
def opal_profiles_list(response: Response, svc: ListenerService = Depends(get_service)) -> list[OpalProfile]:
    with svc.db.session() as session:
        items = [OpalProfile.model_validate(p) for p in catalog.list_profiles(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/",
    response_model=OpalProfile,
    status_code=201,
    responses=_ITEM_ERRORS,
    operation_id="opal_profiles_create",
)
def opal_profiles_create(
    data: OpalProfileCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    with svc.config_change() as session:
        current = [OpalProfile.model_validate(p) for p in catalog.list_profiles(session)]
        require_matching_etag(if_match, collection_etag(current), "profiles")
        result = OpalProfile.model_validate(catalog.create_profile(session, data))
    response.headers["Location"] = f"/api/v1/profiles/{result.id}"
    return result


@router.get(
    "/{profile_id}",
    response_model=OpalProfile,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_profile_get",
)
def opal_profile_get(
    response: Response,
    profile_id: int = Path(ge=1),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    with svc.db.session() as session:
        item = OpalProfile.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{profile_id}",
    response_model=OpalProfile,
    responses=_ITEM_ERRORS,
    operation_id="opal_profile_patch",
)
def opal_profile_patch(
    response: Response,
    profile_id: int = Path(ge=1),
    data: OpalProfilePatch = ...,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    with svc.config_change() as session:
        current = OpalProfile.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        result = OpalProfile.model_validate(catalog.update_profile(session, profile_id, data))
    response.headers["ETag"] = item_etag(result)
    return result


@router.delete("/{profile_id}", status_code=204, responses=_ITEM_ERRORS, operation_id="opal_profile_delete")
def opal_profile_delete(
    profile_id: int = Path(ge=1),
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = OpalProfile.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        catalog.delete_profile(session, profile_id)
    return Response(status_code=204)


@router.post(
    "/{profile_id}/activate",
    response_model=OpalProfile,
    responses=_ITEM_ERRORS,
    operation_id="opal_profile_activate",
)
def opal_profile_activate(
    profile_id: int = Path(ge=1),
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    """Set this profile as active, deactivating all others."""
    with svc.config_change() as session:
        current = OpalProfile.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        result = OpalProfile.model_validate(catalog.activate_profile(session, profile_id))
    return result
