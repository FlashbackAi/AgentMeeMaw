"""SSE streaming endpoints for /turn and /session/start.

The non-streaming JSON variants in :mod:`flashback.http.routes.turn` and
:mod:`flashback.http.routes.session` stay as-is. These routes target Node
(and any other consumer) that wants tokens as soon as the LLM emits them,
while still receiving pre-LLM metadata (intent, taps, chips) before the
text starts flowing.

Wire format: ``text/event-stream`` with these named events:

  - ``meta``        pre-LLM metadata available at request time
  - ``voice_style`` voice mode only: ``{"style": "..."}`` prosody label
                    lifted from the reply's leading ``[[style: x]]`` tag,
                    emitted once before the first ``text_delta``
  - ``text_delta``  ``{"text": "..."}`` token chunks
  - ``done``        final post-LLM payload (carries ``voice_style`` too
                    in voice mode)
  - ``error``       terminal failure with ``code``, ``message``,
                    ``partial_text``

Idempotency is intentionally NOT supported on these endpoints. The Node
side should not retry partial streams; on disconnect, the next user turn
sees the partial assistant text already committed to working memory and
the conversation continues naturally.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_db_pool,
    get_http_config,
    get_orchestrator,
    get_redis,
    get_working_memory,
)
from flashback.http.ground_truth_answer import persist_ground_truth_answer
from flashback.http.models import SessionStartRequest, TurnRequest
from flashback.orchestrator import OrchestratorProtocol
from flashback.orchestrator.errors import WorkingMemoryNotFound
from flashback.orchestrator.protocol import StreamEvent
from flashback.question_decisions import QuestionDecisionRepository
from flashback.working_memory import WorkingMemory

try:
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - imported by app at boot
    AsyncConnectionPool = None  # type: ignore[assignment]

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.stream")


def _format_sse(event: StreamEvent) -> str:
    """Serialize a StreamEvent as one SSE message.

    Two newlines terminate the message per the SSE spec. ``default=str``
    catches UUIDs and datetimes inside ``data`` payloads.
    """
    payload = json.dumps(event.data, default=str)
    return f"event: {event.type}\ndata: {payload}\n\n"


@router.post("/turn/stream")
async def turn_stream(
    body: TurnRequest,
    cfg: HttpConfig = Depends(get_http_config),
    redis: Redis = Depends(get_redis),
    wm: WorkingMemory = Depends(get_working_memory),
    orch: OrchestratorProtocol = Depends(get_orchestrator),
    db_pool: "AsyncConnectionPool" = Depends(get_db_pool),
) -> StreamingResponse:
    """SSE twin of :func:`flashback.http.routes.turn.turn`.

    Pre-stream checks (working memory existence, rate limit,
    question_decision persistence) run synchronously so they can surface
    as proper HTTP error responses. Anything after that flows through
    the SSE body.
    """
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
        mode=body.mode,
    )

    if not await wm.exists(str(body.session_id)):
        raise WorkingMemoryNotFound(
            f"No working memory for session {body.session_id}; "
            "did /session/start succeed?"
        )

    await _enforce_turn_rate_limit(
        redis,
        session_id=str(body.session_id),
        limit_per_minute=cfg.turn_rate_limit_per_minute,
    )

    if body.question_decision is not None:
        repo = QuestionDecisionRepository(db_pool)
        await repo.record(
            person_id=body.person_id,
            question_id=body.question_decision.question_id,
            action=body.question_decision.action,
        )
        await wm.append_asked_question(
            session_id=str(body.session_id),
            question_id=str(body.question_decision.question_id),
        )
        log.info(
            "question_decision.recorded",
            question_id=str(body.question_decision.question_id),
            action=body.question_decision.action,
        )

    if body.ground_truth_answer is not None:
        # Persist before the pipeline runs (mirrors question_decision).
        await persist_ground_truth_answer(
            session_id=body.session_id,
            person_id=body.person_id,
            answer=body.ground_truth_answer,
            wm=wm,
            db_pool=db_pool,
        )

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in orch.handle_turn_stream(
                session_id=body.session_id,
                person_id=body.person_id,
                role_id=body.role_id,
                user_message=body.message,
                mode=body.mode,
            ):
                yield _format_sse(event)
        except Exception as exc:  # noqa: BLE001
            # Pre-LLM step failure: we've already started the streaming
            # response, so HTTPException can't surface as a status code.
            # Emit a terminal error event instead.
            log.error(
                "turn_stream.unhandled",
                error=type(exc).__name__,
                detail=str(exc),
            )
            yield _format_sse(
                StreamEvent(
                    type="error",
                    data={
                        "code": type(exc).__name__,
                        "message": str(exc),
                        "partial_text": "",
                    },
                )
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Disable proxy buffering so deltas reach the client promptly.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/session/start/stream")
async def session_start_stream(
    body: SessionStartRequest,
    orch: OrchestratorProtocol = Depends(get_orchestrator),
) -> StreamingResponse:
    """SSE twin of :func:`flashback.http.routes.session.session_start`."""
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
        mode=body.mode,
    )

    contributor_name = (body.contributor_display_name or "").strip()
    metadata_with_name = dict(body.session_metadata)
    if contributor_name:
        metadata_with_name["contributor_display_name"] = contributor_name

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in orch.handle_session_start_stream(
                session_id=body.session_id,
                person_id=body.person_id,
                role_id=body.role_id,
                session_metadata=metadata_with_name,
                mode=body.mode,
            ):
                yield _format_sse(event)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session_start_stream.unhandled",
                error=type(exc).__name__,
                detail=str(exc),
            )
            yield _format_sse(
                StreamEvent(
                    type="error",
                    data={
                        "code": type(exc).__name__,
                        "message": str(exc),
                        "partial_text": "",
                    },
                )
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _enforce_turn_rate_limit(
    redis: Redis, *, session_id: str, limit_per_minute: int
) -> None:
    if limit_per_minute <= 0:
        return
    minute = int(datetime.now(timezone.utc).timestamp() // 60)
    key = f"rate:turn:{session_id}:{minute}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 70)
    if count > limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="turn rate limit exceeded",
        )
