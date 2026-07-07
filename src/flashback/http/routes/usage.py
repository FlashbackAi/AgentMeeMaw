"""``POST /usage/events`` — Node records its own artifact-generation cost.

The agent is the sole writer of ``usage_events``: Python meters its own
LLM/embedding calls inline, and Node's image/video/voice generation cost
arrives here so the agent performs the insert (Node never writes Postgres
directly — CLAUDE.md §3). ``service`` is forced to ``'node'`` server-side.

No auth dependency: Node is the auth boundary; trust is the service token
plus the private network (invariant #8).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from flashback.http.deps import get_db_pool
from flashback.usage.queries import INSERT_USAGE_EVENT

log = structlog.get_logger("flashback.http.usage")

router = APIRouter(prefix="/usage")


class UsageEventRequest(BaseModel):
    feature: str
    provider: str
    model: str
    cost_usd: float
    unit_type: str = "tokens"
    units: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    person_id: str | None = None
    session_id: str | None = None


class UsageEventResponse(BaseModel):
    id: str


@router.post(
    "/events",
    response_model=UsageEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_usage_event(
    body: UsageEventRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> UsageEventResponse:
    if db_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        )
    row = {
        "service": "node",  # forced: Node cannot post agent-attributed rows
        "feature": body.feature,
        "provider": body.provider,
        "model": body.model,
        "input_tokens": body.input_tokens,
        "output_tokens": body.output_tokens,
        "cache_read_tokens": body.cache_read_tokens,
        "cache_write_tokens": body.cache_write_tokens,
        "units": body.units,
        "unit_type": body.unit_type,
        "cost_usd": body.cost_usd,
        "person_id": body.person_id,
        "session_id": body.session_id,
    }
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(INSERT_USAGE_EVENT, row)
            new_id = (await cur.fetchone())[0]
    return UsageEventResponse(id=new_id)
