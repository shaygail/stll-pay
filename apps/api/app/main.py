"""STLL PAY API application."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from app.admin.routes import router as admin_router
from app.auth.routes import me_router
from app.auth.routes import router as auth_router
from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.merchants.routes import router as merchants_router

log = structlog.get_logger()

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    log.info("api.start", environment=settings.environment,
             payments_provider=settings.payments_provider,
             live_payments_enabled=settings.live_payments_enabled)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="STLL PAY API",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs only outside production.
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "production" else "/openapi.json",
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if settings.is_deployed:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        # Log the route, never bodies, query strings or cookies.
        log.info("http.request", method=request.method, path=request.url.path,
                 status=response.status_code,
                 ms=round((time.perf_counter() - started) * 1000, 1), request_id=request_id)
        return response

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, str]:
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/v1/config", tags=["ops"])
    async def public_config() -> dict[str, object]:
        # Non-secret flags the web app uses to show the test-mode banner.
        return {
            "environment": settings.environment,
            "payments_provider": settings.payments_provider,
            "live_payments_enabled": settings.live_payments_enabled,
        }

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(merchants_router)
    app.include_router(admin_router)
    return app


app = create_app()
