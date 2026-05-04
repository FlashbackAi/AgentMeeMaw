"""Exception handlers — domain errors mapped to HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from flashback.orchestrator.stub import PersonNotFoundError
from flashback.working_memory.client import WorkingMemoryError
from flashback.working_memory.keys import InvalidSessionIdError


def _json_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def install_exception_handlers(app: FastAPI) -> None:
    """Wire domain exceptions to HTTP responses.

    Intentionally narrow — only domain errors that carry a defined
    user-facing meaning are mapped here. Everything else bubbles up to
    FastAPI's default 500 handler so unexpected failures stay loud.
    """

    @app.exception_handler(PersonNotFoundError)
    async def _person_not_found(_: Request, exc: PersonNotFoundError):
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(WorkingMemoryError)
    async def _wm_missing(_: Request, exc: WorkingMemoryError):
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(InvalidSessionIdError)
    async def _invalid_session(_: Request, exc: InvalidSessionIdError):
        return _json_error(status.HTTP_400_BAD_REQUEST, str(exc))
