"""Structured JSON logs. Never log secrets, tokens, bodies or bank details."""

from __future__ import annotations

import logging

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    renderer = (structlog.processors.JSONRenderer() if settings.is_deployed
                else structlog.dev.ConsoleRenderer(colors=False))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)),
    )
