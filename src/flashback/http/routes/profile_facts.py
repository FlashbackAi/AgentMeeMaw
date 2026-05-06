"""``POST /profile_facts/upsert`` — Node-driven fact edits.

Body shape: :class:`flashback.profile_facts.schema.FactUpsertRequest`.

Behaviour:

* If an ``active`` row exists for ``(person_id, fact_key)`` and the new
  ``answer_text`` is identical, returns the existing row's id with no
  write.
* Otherwise, supersedes the prior active row (if any) and inserts a
  new active row with ``source = 'user_edit'``. Pushes a job to the
  ``embedding`` queue for the new row.
* If no row exists and the person is at the 25-active-fact cap,
  responds 409 Conflict (frontend should ask the user to edit an
  existing fact instead of adding a new key).

This endpoint is the agent service's only write surface for profile
facts. Node owns the legacy-page UI; we own the canonical store and
the embedding lifecycle.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_http_config
from flashback.profile_facts.repository import upsert_fact_async
from flashback.profile_facts.schema import (
    FactUpsertRequest,
    FactUpsertResponse,
)
from flashback.profile_facts.seeds import SEED_FACT_QUESTIONS
from flashback.workers.extraction.sqs_client import EmbeddingJobSender

router = APIRouter(
    prefix="/profile_facts",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.profile_facts")


@router.post("/upsert", response_model=FactUpsertResponse)
async def upsert(
    body: FactUpsertRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    cfg: HttpConfig = Depends(get_http_config),
) -> FactUpsertResponse:
    """Upsert one fact for one person."""
    structlog.contextvars.bind_contextvars(
        person_id=str(body.person_id),
        fact_key=body.fact_key,
    )

    if not cfg.embedding_queue_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EMBEDDING_QUEUE_URL not configured",
        )

    question_text = (
        body.question_text
        or SEED_FACT_QUESTIONS.get(body.fact_key)
        or _default_question_text(body.fact_key)
    )

    sender = EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await upsert_fact_async(
                    cur,
                    person_id=str(body.person_id),
                    fact_key=body.fact_key,
                    question_text=question_text,
                    answer_text=body.answer_text,
                    source="user_edit",
                    push_embedding=sender.send,
                    embedding_model=cfg.embedding_model,
                    embedding_model_version=cfg.embedding_model_version,
                    max_active_facts_per_person=getattr(
                        cfg, "profile_facts_max_active_per_person", 25
                    ),
                )

    if result.cap_reached:
        log.info(
            "profile_facts.upsert_rejected_cap",
            person_id=str(body.person_id),
            fact_key=body.fact_key,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "person is at the per-person profile_facts cap; "
                "edit an existing fact instead"
            ),
        )

    log.info(
        "profile_facts.upsert_complete",
        person_id=str(body.person_id),
        fact_key=body.fact_key,
        fact_id=str(result.fact_id),
        skipped_unchanged=result.skipped,
        superseded_id=str(result.superseded_id) if result.superseded_id else None,
    )

    return FactUpsertResponse(
        fact_id=result.fact_id,
        person_id=body.person_id,
        fact_key=body.fact_key,
        superseded_id=result.superseded_id,
        cap_reached=False,
    )


def _default_question_text(fact_key: str) -> str:
    """Fallback phrasing for a non-seed fact_key when the caller didn't
    supply one. The contributor will likely overwrite it from the UI.
    """
    pretty = fact_key.replace("_", " ")
    return f"What about {{name}}'s {pretty}?"
