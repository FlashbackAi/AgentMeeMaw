"""``POST /persons`` -- agent-owned ``persons`` row creation.

Per CLAUDE.md s3 the canonical graph is write-locked to the agent
service. Node hits this endpoint during onboarding once the
contributor has supplied (a) the deceased's display name, (b) their
own relationship to them, and (c) the contributor's own display name.
The new row is inserted with all cold-start defaults
(``phase='starter'``, zeroed coverage, no profile summary, no artifact
URL/prompt yet); the portrait artifact is *not* enqueued here (see
CLAUDE.md s1 -- name + relationship is too thin a prompt).

Auth: ``require_service_token``, same as every other write route.

Idempotency: optional ``Idempotency-Key`` header, scoped per key.
There's no natural-idempotency story (two contributors both creating a
"Robert Smith" legacy must produce two distinct rows), so the header
is the only retry-safety mechanism.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_redis
from flashback.http.idempotency import idempotency_key_header, run_idempotent
from flashback.http.models import PersonCreateRequest, PersonCreateResponse
from flashback.persons import insert_person

router = APIRouter(
    prefix="/persons",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.persons")


@router.post("", response_model=PersonCreateResponse)
async def create(
    body: PersonCreateRequest,
    idempotency_key: str | None = Depends(idempotency_key_header),
    redis: Redis = Depends(get_redis),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> PersonCreateResponse:
    """Insert one ``persons`` row from onboarding data."""
    return await run_idempotent(
        redis,
        scope="person_create",
        key=idempotency_key,
        response_model=PersonCreateResponse,
        operation=lambda: _create_once(
            name=body.name,
            relationship=body.relationship,
            contributor_display_name=body.contributor_display_name,
            db_pool=db_pool,
        ),
    )


async def _create_once(
    *,
    name: str,
    relationship: str,
    contributor_display_name: str,
    db_pool: AsyncConnectionPool,
) -> PersonCreateResponse:
    created = await insert_person(
        db_pool,
        name=name,
        relationship=relationship,
    )
    structlog.contextvars.bind_contextvars(person_id=str(created.person_id))
    log.info(
        "person.created",
        person_id=str(created.person_id),
        phase=created.phase,
        contributor_display_name=contributor_display_name,
    )
    return PersonCreateResponse(
        person_id=created.person_id,
        name=created.name,
        relationship=created.relationship,
        phase=created.phase,  # type: ignore[arg-type]
        created_at=created.created_at,
    )
