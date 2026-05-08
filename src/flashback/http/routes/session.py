"""``/session/start`` and ``/session/wrap`` routes."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from flashback.http.auth import require_service_token
from flashback.http.deps import get_orchestrator, get_redis, get_working_memory
from flashback.http.idempotency import idempotency_key_header, run_idempotent
from flashback.http.models import (
    SessionStartMetadata,
    SessionStartRequest,
    SessionStartResponse,
    SessionWrapMetadata,
    SessionWrapRequest,
    SessionWrapResponse,
)
from flashback.orchestrator import OrchestratorProtocol
from flashback.orchestrator.errors import WorkingMemoryNotFound
from flashback.working_memory import WorkingMemory

router = APIRouter(prefix="/session", dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.session")


@router.post("/start", response_model=SessionStartResponse)
async def session_start(
    body: SessionStartRequest,
    wm: WorkingMemory = Depends(get_working_memory),
    orch: OrchestratorProtocol = Depends(get_orchestrator),
) -> SessionStartResponse:
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
    )

    started_at = datetime.now(timezone.utc)
    seed_summary = body.session_metadata.get("prior_session_summary", "") or ""
    contributor_name = (body.contributor_display_name or "").strip()

    metadata_with_name = dict(body.session_metadata)
    if contributor_name:
        metadata_with_name["contributor_display_name"] = contributor_name

    result = await orch.handle_session_start(
        session_id=body.session_id,
        person_id=body.person_id,
        role_id=body.role_id,
        session_metadata=metadata_with_name,
    )

    if not getattr(orch, "owns_working_memory", False):
        await wm.initialize(
            session_id=str(body.session_id),
            person_id=str(body.person_id),
            role_id=str(body.role_id),
            started_at=started_at,
            seed_prior_session_summary=seed_summary,
            contributor_display_name=contributor_name,
        )
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
            await wm.append_asked_question(
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
    idempotency_key: str | None = Depends(idempotency_key_header),
    redis: Redis = Depends(get_redis),
    wm: WorkingMemory = Depends(get_working_memory),
    orch: OrchestratorProtocol = Depends(get_orchestrator),
) -> SessionWrapResponse:
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
    )

    return await run_idempotent(
        redis,
        scope=f"session_wrap:{body.session_id}",
        key=idempotency_key,
        response_model=SessionWrapResponse,
        operation=lambda: _run_session_wrap(body=body, wm=wm, orch=orch),
    )


async def _run_session_wrap(
    *,
    body: SessionWrapRequest,
    wm: WorkingMemory,
    orch: OrchestratorProtocol,
) -> SessionWrapResponse:
    if not await wm.exists(str(body.session_id)):
        raise WorkingMemoryNotFound(
            f"No working memory for session {body.session_id}; "
            "session was not started or has already been wrapped."
        )

    result = await orch.handle_session_wrap(
        session_id=body.session_id,
        person_id=body.person_id,
    )
    if not getattr(orch, "owns_working_memory", False):
        await wm.clear(str(body.session_id))

    log.info("session.wrap")
    return SessionWrapResponse(
        session_summary=result.session_summary,
        metadata=SessionWrapMetadata(
            segments_extracted_count=result.segments_extracted_count,
        ),
    )
