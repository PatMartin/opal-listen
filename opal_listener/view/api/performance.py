from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from opal_listener.models.opal_standard import OPAL_422_RESPONSE
from opal_listener.models.schemas import PerformanceResultRead, PerformanceSummary
from opal_listener.service import recognition_store
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.pagination import decode_cursor, encode_cursor
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/performance", tags=["performance"])

_SCOPE = "performance_history"


@router.get("/last", response_model=PerformanceResultRead | None, responses=OPAL_422_RESPONSE)
def get_last_performance(svc: ListenerService = Depends(get_service)) -> PerformanceResultRead | None:
    """Return the most recent performance result."""
    with svc.db.session() as session:
        row = recognition_store.last_performance(session)
        return PerformanceResultRead.model_validate(row) if row else None


@router.get("/summary", response_model=PerformanceSummary)
def get_performance_summary(
    limit: int = Query(default=100, le=1000), svc: ListenerService = Depends(get_service)
) -> PerformanceSummary:
    """Aggregate stats over the last N results."""
    with svc.db.session() as session:
        return recognition_store.performance_summary(session, limit)


@router.get("/history")
def get_performance_history(
    limit: int = Query(default=100, ge=1, le=500, description="Maximum items per page."),
    cursor: str = Query(
        default=None, min_length=1, description="Opaque continuation token returned from the preceding page."
    ),
    svc: ListenerService = Depends(get_service),
) -> dict[str, Any]:
    """Use cursor pagination, stable descending time-plus-ID ordering; filters are bound into the cursor."""
    cursor_tuple = decode_cursor(cursor, _SCOPE, {}, InvalidRequestError) if cursor is not None else None
    with svc.db.session() as session:
        rows, has_more = recognition_store.performance_history_page(session, limit, cursor_tuple)
        items = [PerformanceResultRead.model_validate(r).model_dump(mode="json") for r in rows]
    next_cursor = encode_cursor(_SCOPE, {}, rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}
