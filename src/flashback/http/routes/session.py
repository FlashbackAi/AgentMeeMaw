"""``/session/start`` and ``/session/wrap`` routes."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends

from flashback.http.auth import require_service_token
from flashback.http.deps import get_orchestrator, get_working_memory
from flashback.http.models import (
    SessionStartMetadata,
    SessionStartRequest,
    SessionStartResponse,
    SessionWrapMetadata,
    SessionWrapRequest,
    SessionWrapResponse,
)
from flashback.orchestrator import Orchestrator
from flashback.working_memory import WorkingMemory
from flashback.working_memory.client import WorkingMemoryError

router = APIRouter(prefix="/session", dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.session")


@router.post("/start", response_model=SessionStartResponse)
async def session_start(
    body: SessionStartRequest,
    wm: WorkingMemory = Depends(get_working_memory),
    orch: Orchestrator = Depends(get_orchestrator),
) -> SessionStartResponse:
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
    )

    started_at = datetime.now(timezone.utc)
    seed_summary = body.session_metadata.get("prior_session_summary", "") or ""

    # The orchestrator's DB read is what surfaces 404 for unknown person.
    # Doing it before initialising WM means a bad request leaves no
    # orphaned Valkey keys. PersonNotFoundError -> 404 via the global
    # exception handler.
    result = await orch.handle_session_start(
        session_id=body.session_id,
        person_id=body.person_id,
        role_id=body.role_id,
        session_metadata=body.session_metadata,
    )

    await wm.initialize(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
        role_id=str(body.role_id),
        started_at=started_at,
        seed_rolling_summary=seed_summary,
    )
    # The opener is an assistant turn, even if it precedes any user
    # message — log it so the Extraction Worker can attribute the next
    # user response to it via answered_by edges.
    await wm.append_turn(
        session_id=str(body.session_id),
        role="assistant",
        content=result.opener,
        timestamp=started_at,
    )
    await wm.update_signals(
        session_id=str(body.session_id),
        last_opener=result.opener,
    )
    if result.selected_question_id is not None:
        await wm.set_seeded_question(
            session_id=str(body.session_id),
            question_id=str(result.selected_question_id),
        )

    log.info("session.start", phase=result.phase)
    return SessionStartResponse(
        session_id=body.session_id,
        opener=result.opener,
        metadata=SessionStartMetadata(
            phase=result.phase,
            selected_question_id=result.selected_question_id,
        ),
    )


@router.post("/wrap", response_model=SessionWrapResponse)
async def session_wrap(
    body: SessionWrapRequest,
    wm: WorkingMemory = Depends(get_working_memory),
    orch: Orchestrator = Depends(get_orchestrator),
) -> SessionWrapResponse:
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
    )

    if not await wm.exists(str(body.session_id)):
        # Surface as a domain error so the global handler maps to 409.
        raise WorkingMemoryError(
            f"No working memory for session {body.session_id}; "
            "session was not started or has already been wrapped."
        )

    result = await orch.handle_session_wrap(
        session_id=body.session_id,
        person_id=body.person_id,
    )
    await wm.clear(str(body.session_id))

    log.info("session.wrap")
    return SessionWrapResponse(
        session_summary=result.session_summary,
        metadata=SessionWrapMetadata(
            moments_extracted_estimate=result.moments_extracted_estimate,
        ),
    )
