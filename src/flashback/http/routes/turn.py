"""``/turn`` route — the per-message agent surface."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_http_config,
    get_orchestrator,
    get_redis,
    get_working_memory,
)
from flashback.http.idempotency import idempotency_key_header, run_idempotent
from flashback.http.models import TurnMetadata, TurnRequest, TurnResponse
from flashback.orchestrator import OrchestratorProtocol
from flashback.orchestrator.errors import WorkingMemoryNotFound
from flashback.working_memory import WorkingMemory

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.turn")


@router.post("/turn", response_model=TurnResponse)
async def turn(
    body: TurnRequest,
    idempotency_key: str | None = Depends(idempotency_key_header),
    cfg: HttpConfig = Depends(get_http_config),
    redis: Redis = Depends(get_redis),
    wm: WorkingMemory = Depends(get_working_memory),
    orch: OrchestratorProtocol = Depends(get_orchestrator),
) -> TurnResponse:
    structlog.contextvars.bind_contextvars(
        session_id=str(body.session_id),
        person_id=str(body.person_id),
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

    return await run_idempotent(
        redis,
        scope=f"turn:{body.session_id}",
        key=idempotency_key,
        response_model=TurnResponse,
        operation=lambda: _run_turn(body=body, wm=wm, orch=orch),
    )


async def _run_turn(
    *,
    body: TurnRequest,
    wm: WorkingMemory,
    orch: OrchestratorProtocol,
) -> TurnResponse:
    orchestrator_owns_wm = getattr(orch, "owns_working_memory", False)
    if not orchestrator_owns_wm:
        user_ts = datetime.now(timezone.utc)
        await wm.append_turn(
            session_id=str(body.session_id),
            role="user",
            content=body.message,
            timestamp=user_ts,
        )

    result = await orch.handle_turn(
        session_id=body.session_id,
        person_id=body.person_id,
        role_id=body.role_id,
        user_message=body.message,
    )

    if not orchestrator_owns_wm:
        await wm.append_turn(
            session_id=str(body.session_id),
            role="assistant",
            content=result.reply,
            timestamp=datetime.now(timezone.utc),
        )

    log.info(
        "turn.completed",
        intent=result.intent,
        emotional_temperature=result.emotional_temperature,
        segment_boundary=result.segment_boundary,
    )
    return TurnResponse(
        reply=result.reply,
        metadata=TurnMetadata(
            intent=result.intent,
            emotional_temperature=result.emotional_temperature,
            segment_boundary=result.segment_boundary,
        ),
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
