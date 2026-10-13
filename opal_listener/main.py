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
from opal_listener.service.opal_context import get_opal_context
from opal_listener.service.runtime_config import ConfigInvalidError
from opal_listener.view.api import ROUTERS
from opal_listener.view.opal_middleware import OpalContextMiddleware

log = logging.getLogger(__name__)
STATIC_DIR = PACKAGE_DIR / "view" / "static"

# Schemas referenced by the contract but no longer produced by any route (the per-key settings
# endpoints were removed in v1.2); kept so the generated OpenAPI remains a superset of the contract.
_ORPHAN_CONTRACT_SCHEMAS = (SettingRead, SettingUpdate, OpalCursorPageMetadata)


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
        version="1.2.0",
        lifespan=lifespan,
    )
    app.state.listener = service
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
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.exception_handler(ConfigInvalidError)
    async def _config_invalid(_: Request, exc: ConfigInvalidError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": _validation_detail(exc.report.errors, "config_invalid")},
        )

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        if exc.status_code == 422:
            return JSONResponse(status_code=422, content={"detail": _validation_detail([exc.message], "value_error")})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

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
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        log.info("integrity error: %s", exc.orig)
        return JSONResponse(status_code=409, content={"detail": "conflicts with an existing record"})

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
