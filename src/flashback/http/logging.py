"""
Structured logging setup.

JSON output via structlog, with a request-scoped middleware that binds
``session_id`` and ``person_id`` to the context for every log line
emitted while the request is being served.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response


def configure_logging() -> None:
    """One-time process-wide structlog configuration.

    JSON renderer so log aggregation downstream is uniform across the
    Python service and the Node service.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def install_request_logging_middleware(app: FastAPI) -> None:
    """Bind ``session_id`` / ``person_id`` from the JSON body, log a
    single line per request.

    Reading the body in a middleware costs us one extra parse, but the
    structured-log win at this scale (a handful of POSTs per session) is
    worth more than the parse. We only attempt to parse JSON on POST
    routes; GET /health is left alone."""

    log = structlog.get_logger("flashback.http")

    @app.middleware("http")
    async def _bind_and_log(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            method=request.method,
            path=request.url.path,
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "request.failed",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        log.info(
            "request.completed",
            status=response.status_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return response
