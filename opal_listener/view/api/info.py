"""Kernel info routes: service identity and supervisor ping."""

from __future__ import annotations

from fastapi import APIRouter

from opal_listener import __version__
from opal_listener.models.opal_standard import OPAL_422_RESPONSE, LivenessRead, ServiceInfo

router = APIRouter(prefix="/api/v1/info", tags=["info"])


@router.get("/", response_model=ServiceInfo, responses=OPAL_422_RESPONSE, operation_id="opal_info_get")
def opal_info_get() -> ServiceInfo:
    return ServiceInfo(
        name="opal-listen-svc",
        version=__version__,
        api_version="v1",
        description="Wakeword, STT, intent recognition, and slot extraction engine",
    )


@router.get("/ping", response_model=LivenessRead, responses=OPAL_422_RESPONSE, operation_id="opal_info_ping")
def opal_info_ping() -> LivenessRead:
    """Lightweight liveness probe for process supervisors."""
    return LivenessRead(status="ok")
