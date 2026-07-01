"""Collaborator removal / restore endpoints (Node-driven, SP6a)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from flashback.collaborators import (
    RemovalResult,
    RestoreResult,
    remove_collaborator_async,
    restore_collaborator_async,
)
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool
from flashback.http.models import CollaboratorActionRequest

log = structlog.get_logger("flashback.http.collaborators")

router = APIRouter(
    prefix="/collaborators", dependencies=[Depends(require_service_token)]
)


@router.post("/remove", response_model=RemovalResult)
async def remove_collaborator(
    req: CollaboratorActionRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> RemovalResult:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await remove_collaborator_async(
                    cur, person_id=str(req.person_id), user_id=str(req.user_id)
                )
    log.info(
        "collaborator.removed",
        person_id=str(req.person_id),
        user_id=str(req.user_id),
        moments_removed=result.moments_removed,
        entities_removed=result.entities_removed,
        moments_resurrected=result.moments_resurrected,
    )
    return result


@router.post("/restore", response_model=RestoreResult)
async def restore_collaborator(
    req: CollaboratorActionRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> RestoreResult:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await restore_collaborator_async(
                    cur, person_id=str(req.person_id), user_id=str(req.user_id)
                )
    log.info(
        "collaborator.restored",
        person_id=str(req.person_id),
        user_id=str(req.user_id),
        moments_restored=result.moments_restored,
        entities_restored=result.entities_restored,
        moments_re_superseded=result.moments_re_superseded,
    )
    return result
