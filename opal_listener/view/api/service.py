"""Kernel service-process lifecycle routes: start/stop/tail the managed server process.

"The service's server process" is the pidfile-tracked detached child managed by
ServiceProcessManager. When the tracked pid is this process itself (the service was
launched via `service start`), the response is still 200 — termination is deferred
to a BackgroundTask so it lands after the response is sent.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query

from opal_listener.models.opal_standard import (
    OPAL_404_RESPONSE,
    OPAL_422_RESPONSE,
    OPAL_500_RESPONSE,
)
from opal_listener.models.schemas import OpalServiceLogTail, OpalServiceStartOptions, OpalServiceStatus
from opal_listener.service.errors import NotFoundError
from opal_listener.service.lifecycle import ServiceProcessManager
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/service", tags=["service"])


def _manager(svc: ListenerService) -> ServiceProcessManager:
    return ServiceProcessManager(svc.app_settings)


@router.post(
    "/start",
    response_model=OpalServiceStatus,
    responses={**OPAL_422_RESPONSE, **OPAL_500_RESPONSE},
    operation_id="opal_service_start",
)
def opal_service_start(
    background_tasks: BackgroundTasks,
    body: OpalServiceStartOptions | None = Body(None),
    svc: ListenerService = Depends(get_service),
) -> dict:
    manager = _manager(svc)
    status, terminate_self = manager.start(append=body.append if body else False)
    if terminate_self:
        background_tasks.add_task(manager.terminate_self)
    return status


@router.post(
    "/stop",
    response_model=OpalServiceStatus,
    responses={**OPAL_422_RESPONSE, **OPAL_500_RESPONSE},
    operation_id="opal_service_stop",
)
def opal_service_stop(
    background_tasks: BackgroundTasks, svc: ListenerService = Depends(get_service)
) -> dict:
    manager = _manager(svc)
    status, terminate_self = manager.stop()
    if terminate_self:
        background_tasks.add_task(manager.terminate_self)
    return status


@router.get(
    "/log",
    response_model=OpalServiceLogTail,
    responses={**OPAL_404_RESPONSE, **OPAL_422_RESPONSE},
    operation_id="opal_service_log",
)
def opal_service_log(
    lines: int = Query(default=10, ge=1, le=1000), svc: ListenerService = Depends(get_service)
) -> dict:
    manager = _manager(svc)
    tail = manager.tail_log(lines)
    if tail is None:
        raise NotFoundError(f"no service log at {manager.log_file}")
    return tail
