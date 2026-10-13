"""REST view layer: one router per resource, all under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from opal_listener.view.api import (
    backup,
    failed_utterances,
    health,
    intents,
    logs,
    performance,
    profiles,
    recognition,
    regression,
    settings,
    slots,
    wakewords,
)

ROUTERS: list[APIRouter] = [
    wakewords.router,
    intents.router,
    slots.router,
    profiles.router,
    settings.router,
    recognition.router,
    logs.router,
    failed_utterances.router,
    regression.router,
    performance.router,
    health.router,
    backup.router,
]
