from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

from opal_listener.models.opal_standard import OPAL_422_RESPONSE
from opal_listener.models.schemas import PipelineStatus, RecognitionResult, TextTestRequest
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.view.api.deps import get_service

router = APIRouter(prefix="/api/v1/recognition", tags=["recognition"])


@router.post("/test_text", response_model=RecognitionResult)
def test_text(
    data: TextTestRequest,
    save_log: bool = Query(default=True, description="Store the result in recognition logs"),
    svc: ListenerService = Depends(get_service),
) -> RecognitionResult:
    return svc.test_text(data.text, save_log=save_log)


@router.post("/test_audio", response_model=RecognitionResult)
def test_audio(
    file: UploadFile = File(...),
    save_log: bool = Query(default=True, description="Store the result in recognition logs"),
    require_wakeword: bool = Query(default=False, description="Return no_match if no wakeword is detected"),
    svc: ListenerService = Depends(get_service),
) -> RecognitionResult:
    """Test a .wav file through the full STT + intent pipeline."""
    limit = svc.app_settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise InvalidRequestError(f"audio file exceeds {limit} bytes")
    return svc.test_audio(data, save_log=save_log, require_wakeword=require_wakeword)


@router.post("/start", responses=OPAL_422_RESPONSE)
def start_pipeline(svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    return svc.start()


@router.post("/stop", responses=OPAL_422_RESPONSE)
def stop_pipeline(svc: ListenerService = Depends(get_service)) -> dict[str, str]:
    return svc.stop()


@router.get("/status", response_model=PipelineStatus, responses=OPAL_422_RESPONSE)
def get_status(svc: ListenerService = Depends(get_service)) -> PipelineStatus:
    return svc.status()


@router.get("/stream")
async def stream_results(
    request: Request,
    debug: bool = Query(default=False, description="Include audio levels and partial transcripts"),
    svc: ListenerService = Depends(get_service),
) -> EventSourceResponse:
    """Server-Sent Events stream for live recognition results."""
    queue = svc.bus.subscribe(debug=debug)

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            yield {"event": "status", "data": svc.status().model_dump_json()}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                yield {"event": message["event"], "data": json.dumps(message["data"], default=str)}
        finally:
            svc.bus.unsubscribe(queue)

    return EventSourceResponse(events(), ping=15)
