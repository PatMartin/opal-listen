from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import col, select

from opal_listener.models.opal_standard import OPAL_404_RESPONSE, OPAL_422_RESPONSE, OPAL_PRECONDITION_RESPONSES
from opal_listener.models.schemas import (
    RegressionRunResult,
    RegressionTestCreate,
    RegressionTestRead,
    RegressionTestUpdate,
)
from opal_listener.models.tables import RegressionTest
from opal_listener.service import review
from opal_listener.service.catalog import get_or_404
from opal_listener.service.concurrency import collection_etag, item_etag, require_matching_etag
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/regression", tags=["regression"])


def _list(session: object) -> list[RegressionTestRead]:
    rows = session.exec(select(RegressionTest).order_by(col(RegressionTest.id))).all()
    return [RegressionTestRead.model_validate(r) for r in rows]


@router.get("/", response_model=list[RegressionTestRead], responses=OPAL_422_RESPONSE)
def list_regression_tests(response: Response, svc: ListenerService = Depends(get_service)) -> list[RegressionTestRead]:
    with svc.db.session() as session:
        items = _list(session)
    response.headers["ETag"] = collection_etag(items)
    return items


@router.post("/", response_model=RegressionTestRead, status_code=201, responses=OPAL_PRECONDITION_RESPONSES)
def create_regression_test(
    data: RegressionTestCreate,
    response: Response,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> RegressionTestRead:
    with svc.db.session() as session:
        require_matching_etag(if_match, collection_etag(_list(session)), "regression tests")
        obj = RegressionTest(**data.model_dump())
        session.add(obj)
        session.commit()
        result = RegressionTestRead.model_validate(obj)
    response.headers["Location"] = f"/api/v1/regression/{result.id}"
    return result


@router.post("/run", response_model=RegressionRunResult)
def run_regression_tests(enabled_only: bool = True, svc: ListenerService = Depends(get_service)) -> RegressionRunResult:
    """Run all (enabled) regression tests against the current intent model."""
    with svc.db.session() as session:
        return review.run_regression(session, svc, enabled_only)


@router.get("/{test_id}", response_model=RegressionTestRead, responses=OPAL_404_RESPONSE)
def get_regression_test(
    test_id: int, response: Response, svc: ListenerService = Depends(get_service)
) -> RegressionTestRead:
    with svc.db.session() as session:
        item = RegressionTestRead.model_validate(get_or_404(session, RegressionTest, test_id, "regression test"))
    response.headers["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{test_id}", response_model=RegressionTestRead, responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES}
)
def update_regression_test(
    test_id: int,
    data: RegressionTestUpdate,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> RegressionTestRead:
    with svc.db.session() as session:
        obj = get_or_404(session, RegressionTest, test_id, "regression test")
        require_matching_etag(if_match, item_etag(RegressionTestRead.model_validate(obj)), "regression test")
        for key, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(obj, key, value)
        session.add(obj)
        session.commit()
        return RegressionTestRead.model_validate(obj)


@router.delete("/{test_id}", status_code=204, responses={**OPAL_404_RESPONSE, **OPAL_PRECONDITION_RESPONSES})
def delete_regression_test(
    test_id: int,
    if_match: str = Header(..., alias="If-Match", pattern=r'^"[^"]+"$'),
    svc: ListenerService = Depends(get_service),
) -> Response:
    with svc.db.session() as session:
        obj = get_or_404(session, RegressionTest, test_id, "regression test")
        require_matching_etag(if_match, item_etag(RegressionTestRead.model_validate(obj)), "regression test")
        session.delete(obj)
        session.commit()
    return Response(status_code=204)
