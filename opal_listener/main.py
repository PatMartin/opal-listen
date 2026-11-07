"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from opal_listener import __version__
from opal_listener.app_settings import PACKAGE_DIR, AppSettings, get_app_settings
from opal_listener.data.db import Database
from opal_listener.models.opal_standard import OPAL_422_RESPONSE, OpalCursorPageMetadata
from opal_listener.models.schemas import SettingRead, SettingUpdate
from opal_listener.service.errors import (
    DomainError,
    OpalValidationError,
    PreconditionFailedError,
    PreconditionRequiredError,
)
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.opal_context import (
    CAUSATION_HEADER,
    REQUEST_HEADER,
    SESSION_HEADER,
    TRANSACTION_HEADER,
    get_opal_context,
)
from opal_listener.service.runtime_config import ConfigInvalidError
from opal_listener.view.api import ROUTERS
from opal_listener.view.opal_auth import OpalAuthMiddleware
from opal_listener.view.opal_middleware import OpalContextMiddleware

log = logging.getLogger(__name__)
STATIC_DIR = PACKAGE_DIR / "view" / "static"

# Schemas referenced by the contract but no longer produced by any route (the per-key settings
# endpoints were removed in v1.2); kept so the generated OpenAPI remains a superset of the contract.
_ORPHAN_CONTRACT_SCHEMAS = (SettingRead, SettingUpdate, OpalCursorPageMetadata)

# OPAL SUITE STANDARD response-header documentation (opal_listen_v1.4.0.yaml components/headers).
# OpalContextMiddleware stamps these on every response, so every operation documents them.
_OPAL_HEADER_COMPONENTS: dict[str, Any] = {
    "OpalSessionIdHeader": {
        "description": "Effective OPAL session context for this response. Returned when a session"
        " identifier is known; conditionally present.",
        "required": False,
        "schema": {"type": "string"},
    },
    "OpalTransactionIdHeader": {
        "description": "Logical transaction identifier for this exchange. Always returned; generated"
        " when the inbound request lacks one.",
        "required": True,
        "schema": {"type": "string"},
    },
    "OpalRequestIdHeader": {
        "description": "Identifier of this inbound HTTP exchange. Always returned; regenerated for each"
        " downstream hop.",
        "required": True,
        "schema": {"type": "string"},
    },
    "OpalCausationIdHeader": {
        "description": "Immediate parent event/request identifier for this response. Returned when"
        " applicable; conditionally present.",
        "required": False,
        "schema": {"type": "string"},
    },
}
_OPAL_RESPONSE_HEADERS = {
    SESSION_HEADER: {"$ref": "#/components/headers/OpalSessionIdHeader"},
    TRANSACTION_HEADER: {"$ref": "#/components/headers/OpalTransactionIdHeader"},
    REQUEST_HEADER: {"$ref": "#/components/headers/OpalRequestIdHeader"},
    CAUSATION_HEADER: {"$ref": "#/components/headers/OpalCausationIdHeader"},
}

_ETAG_EXAMPLE = '"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"'


def _etag_doc(description: str) -> dict[str, Any]:
    return {"description": description, "example": _ETAG_EXAMPLE, "schema": {"type": "string"}}


_COLLECTION_ETAG = _etag_doc("Strong revision of this collection's portable configuration.")
_ITEM_ETAG = _etag_doc(
    "Strong revision of this resource's portable configuration; required as If-Match for its mutations."
)
_MEMBER_ETAG = _etag_doc("Strong revision of this collection; required as If-Match for member deletions.")

# Operations that return a strong ETag (set in code via collection_etag/item_etag), per operation.
_ETAG_OPERATION_HEADERS: dict[str, dict[str, Any]] = {
    "list_wakewords_api_v1_wakewords__get": _COLLECTION_ETAG,
    "list_intents_api_v1_intents__get": _COLLECTION_ETAG,
    "list_slots_api_v1_slots__get": _COLLECTION_ETAG,
    "list_profiles_api_v1_profiles__get": _COLLECTION_ETAG,
    "get_wakeword_api_v1_wakewords__wakeword_id__get": _ITEM_ETAG,
    "get_intent_api_v1_intents__intent_id__get": _ITEM_ETAG,
    "get_slot_api_v1_slots__slot_id__get": _ITEM_ETAG,
    "get_profile_api_v1_profiles__profile_id__get": _ITEM_ETAG,
    "list_phrases_api_v1_intents__intent_id__phrases_get": _MEMBER_ETAG,
    "list_slot_values_api_v1_slots__slot_id__values_get": _MEMBER_ETAG,
    "list_aliases_api_v1_slots__slot_id__values__value_id__aliases_get": _MEMBER_ETAG,
    "get_all_settings_api_v1_settings__get": _etag_doc(
        "Strong revision of the whole settings map; required as If-Match for PATCH /api/v1/settings/."
    ),
    "patch_listen_settings": _etag_doc("Updated settings-map revision after the atomic patch."),
}

# Operations that set a Location header on their 201 responses.
_LOCATION_OPERATION_HEADERS: dict[str, dict[str, Any]] = {
    **{
        opid: {"description": "Resource URL", "schema": {"type": "string"}}
        for opid in (
            "create_wakeword_api_v1_wakewords__post",
            "create_intent_api_v1_intents__post",
            "add_phrase_api_v1_intents__intent_id__phrases_post",
            "create_slot_api_v1_slots__post",
            "add_slot_value_api_v1_slots__slot_id__values_post",
            "add_alias_api_v1_slots__slot_id__values__value_id__aliases_post",
            "create_profile_api_v1_profiles__post",
            "create_failed_utterance_api_v1_failed_utterances__post",
            "create_regression_test_api_v1_regression__post",
        )
    },
    "create_backup_api_v1_backup_create_post": {
        "description": "URI of the created backup (/api/v1/backup/download/{filename}).",
        "schema": {"type": "string"},
    },
}


# BearerAuth security scheme + auth-failure response documentation, mirroring the shared kernel
# (openapi/shared/opal_kernel.yaml). OpalAuthMiddleware enforces these at runtime when
# OPAL_LISTENER_API_TOKEN is configured.
_BEARER_AUTH_SCHEME: dict[str, Any] = {
    "type": "http",
    "scheme": "bearer",
    "bearerFormat": "opaque",
    "description": "Optional on loopback deployments; required on non-loopback interfaces. Bearer"
    " credentials are configured via token_env. Correlation headers are transport context, never"
    " authorization credentials.",
}
_AUTH_FAILURE_RESPONSES: dict[str, dict[str, Any]] = {
    "401": {
        "description": "Missing or invalid authentication credentials.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OpalError"}}},
    },
    "403": {
        "description": "Authenticated but not permitted for this operation.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OpalError"}}},
    },
}


def _inject_security_requirements(schema: dict[str, Any]) -> None:
    """Document BearerAuth globally and 401/403 on every operation (contract-level security)."""
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = _BEARER_AUTH_SCHEME
    schema["security"] = [{"BearerAuth": []}]
    for item in schema.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status, response_doc in _AUTH_FAILURE_RESPONSES.items():
                operation["responses"].setdefault(status, response_doc)


def _inject_documented_response_headers(schema: dict[str, Any]) -> None:
    """Document the response headers the runtime actually emits.

    OpalContextMiddleware stamps the four X-Opal-* correlation headers on every response
    (success, error, and streaming alike); ETag-bearing reads and Location-bearing creates
    are annotated per operation, matching opal_listen_v1.4.0.yaml.
    """
    components = schema.setdefault("components", {})
    components.setdefault("headers", {}).update(_OPAL_HEADER_COMPONENTS)
    for item in schema.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            opid = operation.get("operationId", "")
            for status, response in operation["responses"].items():
                if not isinstance(response, dict):
                    continue
                headers = response.setdefault("headers", {})
                headers.update(_OPAL_RESPONSE_HEADERS)
                if status == "200" and opid in _ETAG_OPERATION_HEADERS:
                    headers["ETag"] = _ETAG_OPERATION_HEADERS[opid]
                if status == "201" and opid in _LOCATION_OPERATION_HEADERS:
                    headers["Location"] = _LOCATION_OPERATION_HEADERS[opid]


def _validation_detail(messages: list[str], error_type: str) -> list[dict[str, object]]:
    return [{"loc": ["body"], "msg": m, "type": error_type} for m in messages]


def _opal_error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    ctx = get_opal_context(request)
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": ctx.request_id,
        "transaction_id": ctx.transaction_id,
    }
    if ctx.session_id:
        error["session_id"] = ctx.session_id
    if ctx.causation_id:
        error["causation_id"] = ctx.causation_id
    return JSONResponse(status_code=status_code, content={"error": error})


def _is_missing_if_match(exc: RequestValidationError) -> bool:
    for err in exc.errors():
        loc = tuple(str(p).lower() for p in err.get("loc", ()))
        if len(loc) >= 2 and loc[0] == "header" and loc[1] == "if-match":
            return True
    return False


def _inject_orphan_schemas(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in _ORPHAN_CONTRACT_SCHEMAS:
        name = model.__name__
        if name in components:
            continue
        sub = model.model_json_schema(ref_template="#/components/schemas/{model}")
        defs = sub.pop("$defs", {})
        components[name] = sub
        for def_name, def_schema in defs.items():
            components.setdefault(def_name, def_schema)


def create_app(settings: AppSettings | None = None, service: ListenerService | None = None) -> FastAPI:
    settings = settings or get_app_settings()
    if service is None:
        settings.ensure_dirs()
        service = ListenerService(Database(settings.database_url), settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.startup()
        try:
            yield
        finally:
            service.shutdown()
            service.db.dispose()

    app = FastAPI(
        title="Voice Intent API",
        description="Wakeword, STT, intent recognition, and slot extraction engine",
        version="1.4.0",
        lifespan=lifespan,
    )
    app.state.listener = service
    # Registration order is reversed at runtime: OpalContext runs first (correlation ids exist
    # for auth-failure envelopes and get stamped on the 401s), CORS answers preflights next, and
    # OpalAuth guards the actual routes last.
    app.add_middleware(OpalAuthMiddleware, token=settings.api_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(OpalContextMiddleware)

    default_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = default_openapi()
        _inject_orphan_schemas(schema)
        _inject_security_requirements(schema)
        _inject_documented_response_headers(schema)
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.exception_handler(ConfigInvalidError)
    async def _config_invalid(_: Request, exc: ConfigInvalidError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": _validation_detail(exc.report.errors, "config_invalid")},
        )

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        if exc.status_code == 422:
            return JSONResponse(status_code=422, content={"detail": _validation_detail([exc.message], "value_error")})
        return _opal_error_response(request, exc.status_code, getattr(exc, "error_code", "bad_request"), exc.message)

    async def _opal_standard_error(
        request: Request, exc: PreconditionRequiredError | PreconditionFailedError | OpalValidationError
    ) -> JSONResponse:
        return _opal_error_response(request, exc.status_code, exc.error_code, exc.message)

    app.add_exception_handler(PreconditionRequiredError, _opal_standard_error)
    app.add_exception_handler(PreconditionFailedError, _opal_standard_error)
    app.add_exception_handler(OpalValidationError, _opal_standard_error)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # A truly-required strong If-Match header must yield 428 (OPAL standard), not the
        # generic 422 FastAPI produces for other request validation failures.
        if _is_missing_if_match(exc):
            return _opal_error_response(request, 428, "precondition_required", "missing or empty If-Match header")
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        log.info("integrity error: %s", exc.orig)
        return _opal_error_response(request, 409, "conflict", "conflicts with an existing record")

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Contract: unexpected failures surface as the OpalError envelope (kernel InternalError),
        # not a bare 500. ServerErrorMiddleware still re-raises so the traceback gets logged.
        log.exception("unhandled error during %s %s", request.method, request.url.path)
        return _opal_error_response(request, 500, "internal_error", "internal server error")

    for router in ROUTERS:
        app.include_router(router)
    app.mount("/ui", StaticFiles(directory=STATIC_DIR), name="ui")

    @app.get("/", responses=OPAL_422_RESPONSE)
    def root() -> dict[str, str]:
        return {"name": "opal_listener", "version": __version__, "docs": "/docs", "ui": "/ui/opal-listen.html"}

    @app.get("/healthz", responses=OPAL_422_RESPONSE)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
