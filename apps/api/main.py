"""HTTP and server-sent-event application entrypoint."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.http import (
    RequestContextMiddleware,
    application_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from apps.api.identity import validate_private_preview_identity_configuration
from apps.api.routes.active_target import router as active_target_router
from apps.api.routes.health import router as health_router
from apps.api.routes.meta import router as meta_router
from apps.api.routes.premarket import router as premarket_router
from apps.api.routes.research import router as research_router
from apps.api.routes.research_sessions import router as research_sessions_router
from apps.api.routes.research_sessions_v2 import (
    legacy_report_router as research_sessions_v2_legacy_report_router,
)
from apps.api.routes.research_sessions_v2 import router as research_sessions_v2_router
from apps.api.routes.today_stream import router as today_stream_router
from apps.api.runtime import database_url_from_environment
from stock_assistant.common.errors import ApplicationError
from stock_assistant.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.assert_safe_production()
    database_url = database_url_from_environment(settings.database_url.get_secret_value())
    engine = create_async_engine(database_url)
    password = make_url(database_url).password
    if not password:
        raise RuntimeError("API_CURSOR_SIGNING_MATERIAL_UNAVAILABLE")
    app.state.db_engine = engine
    app.state.cursor_signing_key = hashlib.sha256(
        b"stockassistant:r1:api-cursor:v1\x00" + password.encode("utf-8")
    ).digest()
    try:
        yield
    finally:
        await engine.dispose()


def _mount_routers(application: FastAPI, *, private_preview: bool) -> None:
    if private_preview:
        application.include_router(research_sessions_v2_router, prefix="/api/v2")
        application.include_router(health_router, prefix="/api/v1")
        return
    application.include_router(active_target_router, prefix="/api/v1")
    application.include_router(premarket_router, prefix="/api/v1")
    application.include_router(research_router, prefix="/api/v1")
    application.include_router(research_sessions_router, prefix="/api/v1")
    application.include_router(research_sessions_v2_router, prefix="/api/v2")
    application.include_router(research_sessions_v2_legacy_report_router, prefix="/api/v2")
    application.include_router(today_stream_router, prefix="/api/v1")
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(meta_router, prefix="/api/v1")


def _framework_endpoint_urls(
    *, private_preview: bool, app_env: str
) -> tuple[str | None, str | None]:
    if private_preview:
        return None, None
    return ("/docs" if app_env != "production" else None), "/openapi.json"


settings = get_settings()
if settings.enable_r2_private_preview:
    validate_private_preview_identity_configuration(settings)
docs_url, openapi_url = _framework_endpoint_urls(
    private_preview=settings.enable_r2_private_preview,
    app_env=settings.app_env,
)
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Evidence-grounded A-share research assistant API",
    docs_url=docs_url,
    openapi_url=openapi_url,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(settings.web_origin).rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "If-Match",
        "Last-Event-ID",
        "X-Correlation-ID",
        "X-Request-ID",
    ],
    expose_headers=["ETag", "Location", "X-Correlation-ID", "X-Request-ID"],
)
app.add_middleware(RequestContextMiddleware)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(ApplicationError, application_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
_mount_routers(app, private_preview=settings.enable_r2_private_preview)
