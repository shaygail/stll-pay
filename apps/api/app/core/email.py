"""Outbound email. Real delivery (for example Resend) is added before launch."""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from app.core.config import get_settings

log = structlog.get_logger()


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if settings.email_backend == "file":
        outbox = Path(settings.email_outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        name = f"{time.time_ns()}.json"
        (outbox / name).write_text(json.dumps({"to": to, "subject": subject, "body": body}))
        return
    if settings.is_deployed:  # pragma: no cover - guarded until a provider is configured
        raise RuntimeError("No email provider configured for this environment")
    log.info("email.console", to=to, subject=subject, body=body)
