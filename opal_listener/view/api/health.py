from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OpalHealthProbe
from opal_listener.models.schemas import AudioDevice, HealthReport
from opal_listener.service.audio.capture import list_input_devices
from opal_listener.service.health import run_health_checks
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/health", tags=["health"])

_PROBE_503 = {503: {"model": OpalHealthProbe, "description": "Service unavailable"}}


@router.get("/", response_model=HealthReport, responses=OPAL_422_RESPONSE)
def health_check(svc: ListenerService = Depends(get_service)) -> HealthReport:
    return run_health_checks(svc)


@router.get("/audio_devices", response_model=list[AudioDevice], responses=OPAL_422_RESPONSE)
def get_audio_devices() -> list[AudioDevice]:
    return [AudioDevice(**d) for d in list_input_devices()]


@router.get(
    "/live",
    response_model=OpalHealthProbe,
    operation_id="get_listen_health_live",
    summary="Liveness probe",
    responses={**_PROBE_503, **OPAL_422_RESPONSE},
)
def get_listen_health_live(response: Response, svc: ListenerService = Depends(get_service)) -> OpalHealthProbe:
    """Cheap liveness check: the process is up and the database is reachable."""
    try:
        svc.db.ping()
    except Exception as exc:  # noqa: BLE001
        response.status_code = 503
        return OpalHealthProbe(status="unavailable", ready=False, details={"error": str(exc)})
    return OpalHealthProbe(status="ok", ready=True)


@router.get(
    "/ready",
    response_model=OpalHealthProbe,
    operation_id="get_listen_health_ready",
    summary="Readiness probe",
    responses={**_PROBE_503, **OPAL_422_RESPONSE},
)
def get_listen_health_ready(response: Response, svc: ListenerService = Depends(get_service)) -> OpalHealthProbe:
    """Full readiness check: configuration compiles, the listener provider loads, etc."""
    report = run_health_checks(svc)
    if report.status == "error":
        response.status_code = 503
        failures = [c.detail for c in report.checks if c.status == "error"]
        return OpalHealthProbe(status="unavailable", ready=False, details={"failures": failures})
    if report.status == "warning":
        return OpalHealthProbe(status="degraded", ready=True)
    return OpalHealthProbe(status="ok", ready=True)
