"""SP5 same-event link + contradiction review endpoints (Node-driven)."""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool
from flashback.moment_links import ContradictionItem, SameEventLink
from flashback.moment_links.repository import (
    acknowledge_event_link_async,
    dismiss_contradiction_async,
    list_contradictions_async,
    list_event_links_async,
    unlink_event_link_async,
)

log = structlog.get_logger("flashback.http.moment_links")

event_links_router = APIRouter(
    prefix="/event_links", dependencies=[Depends(require_service_token)]
)
contradictions_router = APIRouter(
    prefix="/contradictions", dependencies=[Depends(require_service_token)]
)


@event_links_router.get("", response_model=list[SameEventLink])
async def list_event_links(
    person_id: UUID,
    include_acknowledged: bool = False,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> list[SameEventLink]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            return await list_event_links_async(
                cur,
                person_id=str(person_id),
                include_acknowledged=include_acknowledged,
            )


@event_links_router.post("/{link_id}/acknowledge")
async def acknowledge_event_link(
    link_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> dict:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                ok = await acknowledge_event_link_async(cur, link_id=str(link_id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "active link not found")
    return {"link_id": str(link_id), "acknowledged": True}


@event_links_router.post("/{link_id}/unlink")
async def unlink_event_link(
    link_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> dict:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                ok = await unlink_event_link_async(cur, link_id=str(link_id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "active link not found")
    return {"link_id": str(link_id), "unlinked": True}


@contradictions_router.get("", response_model=list[ContradictionItem])
async def list_contradictions(
    person_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> list[ContradictionItem]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            return await list_contradictions_async(cur, person_id=str(person_id))


@contradictions_router.post("/{item_id}/dismiss")
async def dismiss_contradiction(
    item_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> dict:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                ok = await dismiss_contradiction_async(cur, item_id=str(item_id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pending contradiction not found")
    return {"item_id": str(item_id), "dismissed": True}
