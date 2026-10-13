from __future__ import annotations

from fastapi import Request

from opal_listener.service.listener_service import ListenerService


def get_service(request: Request) -> ListenerService:
    return request.app.state.listener
