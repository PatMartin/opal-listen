"""Shared schemas for the OPAL SUITE STANDARD v1.2 (correlation errors, health probes, cursor pages).

OpalError and OpalCursorPageMetadata override __get_pydantic_json_schema__ to emit the exact inline
shape the OPAL standard mandates (nested "error" object, JSON Schema 2020-12 ``type: [string, null]``
unions) instead of pydantic's default ``$ref``/``anyOf`` forms.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from pydantic_core import CoreSchema


class OpalErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str
    transaction_id: str
    session_id: str | None = None
    causation_id: str | None = None


class OpalError(BaseModel):
    """Standard OPAL error envelope used for header/precondition/standard-level failures."""

    error: OpalErrorBody

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: Any) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["error"],
            "properties": {
                "error": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "message", "request_id", "transaction_id"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {},
                        "request_id": {"type": "string"},
                        "transaction_id": {"type": "string"},
                        "session_id": {"type": ["string", "null"]},
                        "causation_id": {"type": ["string", "null"]},
                    },
                }
            },
        }


class OpalHealthProbe(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    ready: bool
    details: dict[str, Any] | None = None


class OpalCursorPageMetadata(BaseModel):
    next_cursor: str | None
    has_more: bool

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: Any) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["next_cursor", "has_more"],
            "properties": {
                "next_cursor": {"type": ["string", "null"]},
                "has_more": {"type": "boolean"},
            },
        }


# Reusable OpenAPI response overrides.
OPAL_422_RESPONSE: dict[int | str, dict[str, Any]] = {
    422: {"model": OpalError, "description": "OPAL standard error"},
}
OPAL_PRECONDITION_RESPONSES: dict[int | str, dict[str, Any]] = {
    412: {"model": OpalError, "description": "OPAL standard error"},
    428: {"model": OpalError, "description": "OPAL standard error"},
}
