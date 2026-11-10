from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from opal_listener.models.opal_standard import OPAL_422_RESPONSE
from opal_listener.models.schemas import PerformanceHistory, PerformanceResultRead, PerformanceSummary
from opal_listener.service import recognition_store
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
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


@router.get("/history", response_model=PerformanceHistory)
def get_performance_history(
    limit: int = Query(default=100, ge=1, le=500, description="Maximum items to return."),
    svc: ListenerService = Depends(get_service),
) -> PerformanceHistory:
    """Bounded list in stable descending time-plus-ID order; `limit` caps the returned items."""
    with svc.db.session() as session:
        rows = recognition_store.performance_history(session, limit)
        return PerformanceHistory([PerformanceResultRead.model_validate(r) for r in rows])
