from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.schemas import ProfileCreate, ProfileRead, ProfileUpdate
from opal_listener.models.tables import Profile
from opal_listener.service import catalog
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])


@router.get("/", response_model=list[ProfileRead], responses=OPAL_422_RESPONSE)
def list_profiles(response: Response, svc: ListenerService = Depends(get_service)) -> list[ProfileRead]:
    with svc.db.session() as session:
        items = [ProfileRead.model_validate(p) for p in catalog.list_profiles(session)]
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/", response_model=ProfileRead, status_code=201)
def create_profile(data: ProfileCreate, response: Response, svc: ListenerService = Depends(get_service)) -> ProfileRead:
    with svc.config_change() as session:
        result = ProfileRead.model_validate(catalog.create_profile(session, data))
    response.headers["Location"] = f"/api/v1/profiles/{result.id}"
    return result


@router.get("/{profile_id}", response_model=ProfileRead)
def get_profile(profile_id: int, response: Response, svc: ListenerService = Depends(get_service)) -> ProfileRead:
    with svc.db.session() as session:
        item = ProfileRead.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch("/{profile_id}", response_model=ProfileRead, responses=OPAL_PRECONDITION_RESPONSES)
def update_profile(
    profile_id: int,
    data: ProfileUpdate,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> ProfileRead:
    with svc.config_change() as session:
        current = ProfileRead.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        result = ProfileRead.model_validate(catalog.update_profile(session, profile_id, data))
    return result


@router.delete("/{profile_id}", status_code=204, responses=OPAL_PRECONDITION_RESPONSES)
def delete_profile(
    profile_id: int,
    if_match: str = Header(..., alias="If-Match", min_length=1),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.config_change() as session:
        current = ProfileRead.model_validate(catalog.get_or_404(session, Profile, profile_id, "profile"))
        require_matching_etag(if_match, item_etag(current), "profile")
        catalog.delete_profile(session, profile_id)
    return Response(status_code=204)


@router.post("/{profile_id}/activate", response_model=ProfileRead)
def activate_profile(profile_id: int, svc: ListenerService = Depends(get_service)) -> ProfileRead:
    """Set this profile as active, deactivating all others."""
    with svc.config_change() as session:
        result = ProfileRead.model_validate(catalog.activate_profile(session, profile_id))
    return result
