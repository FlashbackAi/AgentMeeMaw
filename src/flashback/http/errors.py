"""Exception handlers — domain errors mapped to HTTP responses."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from flashback.llm.errors import LLMError
from flashback.orchestrator.errors import PersonNotFound, WorkingMemoryNotFound
from flashback.phase_gate import PhaseGateError
from flashback.working_memory.client import WorkingMemoryError
from flashback.working_memory.keys import InvalidSessionIdError

log = structlog.get_logger("flashback.http.errors")


def _json_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def install_exception_handlers(app: FastAPI) -> None:
    """Wire domain exceptions to HTTP responses.

    Intentionally narrow — only domain errors that carry a defined
    user-facing meaning are mapped here. Everything else bubbles up to
    FastAPI's default 500 handler so unexpected failures stay loud.
    """

    @app.exception_handler(PersonNotFound)
    async def _person_not_found(_: Request, exc: PersonNotFound):
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(WorkingMemoryNotFound)
    async def _orch_wm_missing(_: Request, exc: WorkingMemoryNotFound):
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(WorkingMemoryError)
    async def _wm_missing(_: Request, exc: WorkingMemoryError):
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(InvalidSessionIdError)
    async def _invalid_session(_: Request, exc: InvalidSessionIdError):
        return _json_error(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError):
        log.error("response generation failed", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "service_unavailable",
                "detail": "response generation failed",
            },
        )

    @app.exception_handler(PhaseGateError)
    async def _phase_gate_error(_: Request, exc: PhaseGateError):
        log.error("phase gate failed", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "service_unavailable",
                "detail": "phase gate selection failed",
            },
        )
