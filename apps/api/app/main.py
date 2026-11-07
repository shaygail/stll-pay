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
from app.core.platform import multi_merchant_enabled
from app.merchants.routes import router as merchants_router
from app.payments.routes_checkout import mock_bank as mock_bank_router
from app.payments.routes_checkout import router as checkout_router
from app.payments.routes_merchant import router as payments_router
from app.payments.routes_pos import router as pos_router
from app.payments.routes_webhooks import router as webhooks_router

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
        # Non-secret flags the web app uses (test-mode banner, single-merchant screens).
        async with get_sessionmaker()() as s:
            multi = await multi_merchant_enabled(s)
        return {
            "environment": settings.environment,
            "payments_provider": settings.payments_provider,
            "live_payments_enabled": settings.live_payments_enabled,
            "multi_merchant_enabled": multi,
        }

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(merchants_router)
    app.include_router(admin_router)
    app.include_router(payments_router)
    app.include_router(pos_router)
    app.include_router(checkout_router)
    app.include_router(webhooks_router)
    if settings.payments_provider == "mock" and settings.environment != "production":
        app.include_router(mock_bank_router)
    return app


app = create_app()
