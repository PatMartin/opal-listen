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

router = APIRouter(prefix="/api/v1/profile", tags=["profiles"])

_ITEM_ERRORS = {**OPAL_404_RESPONSE, **OPAL_409_RESPONSE, **OPAL_422_RESPONSE, **OPAL_PRECONDITION_RESPONSES}


@router.get(
    "/",
    response_model=list[OpalProfile],
    response_model_exclude_unset=True,
    responses=OPAL_422_RESPONSE,
    operation_id="opal_profile_list",
)
def opal_profile_list(response: Response, svc: ListenerService = Depends(get_service)) -> list[OpalProfile]:
    with svc.db.session() as session:
        items = [catalog.profile_read(p) for p in catalog.list_profiles(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post(
    "/",
    response_model=OpalProfile,
    response_model_exclude_unset=True,
    status_code=201,
    responses=_ITEM_ERRORS,
    operation_id="opal_profile_create",
)
def opal_profile_create(
    data: OpalProfileCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    with svc.config_change() as session:
        current = [catalog.profile_read(p) for p in catalog.list_profiles(session)]
        require_matching_etag(if_match, collection_etag(current), "profiles")
        result = catalog.profile_read(catalog.create_profile(session, data))
    response.headers["Location"] = f"/api/v1/profile/{result.id}"
    return result


@router.get(
    "/{profile_id}",
    response_model=OpalProfile,
    response_model_exclude_unset=True,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_profile_get",
)
def opal_profile_get(
    response: Response,
    profile_id: str = Path(min_length=1),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    with svc.db.session() as session:
        item = catalog.profile_read(catalog.get_by_ref(session, Profile, profile_id, "profile"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{profile_id}",
    response_model=OpalProfile,
    response_model_exclude_unset=True,
    responses=_ITEM_ERRORS,
    operation_id="opal_profile_patch",
)
def opal_profile_patch(
    response: Response,
    profile_id: str = Path(min_length=1),
    data: OpalProfilePatch = ...,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    with svc.config_change() as session:
        current = catalog.profile_read(catalog.get_by_ref(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        result = catalog.profile_read(catalog.update_profile(session, catalog.parse_id(profile_id, "profile"), data))
    response.headers["ETag"] = item_etag(result)
    return result


@router.delete("/{profile_id}", status_code=204, responses=_ITEM_ERRORS, operation_id="opal_profile_delete")
def opal_profile_delete(
    profile_id: str = Path(min_length=1),
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = catalog.profile_read(catalog.get_by_ref(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        catalog.delete_profile(session, catalog.parse_id(profile_id, "profile"))
    return Response(status_code=204)


@router.post(
    "/{profile_id}/activate",
    response_model=OpalProfile,
    response_model_exclude_unset=True,
    responses=_ITEM_ERRORS,
    operation_id="opal_profile_activate",
)
def opal_profile_activate(
    profile_id: str = Path(min_length=1),
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> OpalProfile:
    """Set this profile as active, deactivating all others."""
    with svc.config_change() as session:
        current = catalog.profile_read(catalog.get_by_ref(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        result = catalog.profile_read(catalog.activate_profile(session, catalog.parse_id(profile_id, "profile")))
    return result
