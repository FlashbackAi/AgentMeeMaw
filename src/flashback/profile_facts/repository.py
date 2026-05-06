"""Read/write helpers for ``profile_facts``.

All writes go through :func:`upsert_fact`, which enforces:

* The 25-active-fact cap per person. New keys are rejected at the cap;
  updates to existing keys are always allowed (they replace, not add).
* Supersession via status flip, never a destructive UPDATE.
* Embedding push to the ``embedding`` queue after the new active row
  is committed (CLAUDE.md invariant #4 — never embed inline).

The function is sync because the profile_summary worker is sync. The
HTTP edit endpoint runs it inside ``asyncio.to_thread``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from .queries import (
    COUNT_ACTIVE_FACTS,
    INSERT_FACT,
    SELECT_ACTIVE_FACT_BY_KEY,
    SUPERSEDE_ACTIVE_FACT,
)

log = structlog.get_logger("flashback.profile_facts.repository")

# Hard cap on active facts per person. Updates to existing keys never
# count against the cap; only new keys do. Tune by editing this constant
# — kept in code rather than a settings field so the value is greppable.
MAX_ACTIVE_FACTS_PER_PERSON: int = 25


class _EmbeddingPusher(Protocol):
    """Subset of :class:`EmbeddingJobSender` we need.

    Both the extraction-worker producer
    (:class:`flashback.workers.extraction.sqs_client.EmbeddingJobSender`)
    and the embedding-worker CLI client
    (:class:`flashback.workers.embedding.sqs_client.SQSClient`) satisfy
    this — they have aligned ``send`` / ``send_embedding_job`` shapes.
    The repository takes a callable to keep the dependency direction
    correct (``profile_facts`` does not import either worker).
    """

    def __call__(
        self,
        *,
        record_type: str,
        record_id: str,
        source_text: str,
        embedding_model: str,
        embedding_model_version: str,
    ) -> None: ...


@dataclass(frozen=True)
class UpsertResult:
    fact_id: UUID
    superseded_id: UUID | None
    cap_reached: bool  # True iff a NEW key was rejected by the cap
    skipped: bool  # True iff the answer matches the existing active row


def count_active_facts(cursor, *, person_id: str) -> int:
    """Count active facts for one person."""
    cursor.execute(COUNT_ACTIVE_FACTS, {"person_id": person_id})
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def upsert_fact(
    cursor,
    *,
    person_id: str,
    fact_key: str,
    question_text: str,
    answer_text: str,
    source: str,
    push_embedding: _EmbeddingPusher,
    embedding_model: str,
    embedding_model_version: str,
    max_active_facts_per_person: int = MAX_ACTIVE_FACTS_PER_PERSON,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    prompt_version: str | None = None,
) -> UpsertResult:
    """Write or supersede a single fact for one person.

    Caller owns the transaction. The embedding push happens AFTER the
    insert. If the caller's transaction rolls back, the embedding job
    will reference a row that doesn't exist; the embedding worker's
    version-guarded UPDATE returns 0 rows in that case and acks
    cleanly, so this is safe (matches the pattern in
    ``workers/extraction/post_commit.py``).
    """

    # 1. Look up any active row for (person, key).
    cursor.execute(
        SELECT_ACTIVE_FACT_BY_KEY,
        {"person_id": person_id, "fact_key": fact_key},
    )
    existing = cursor.fetchone()

    if existing is not None:
        existing_id, _existing_question, existing_answer, _existing_source = existing
        if existing_answer.strip() == answer_text.strip():
            # No change. Skip — don't churn embeddings or supersession history.
            log.info(
                "profile_facts.upsert_skipped_unchanged",
                person_id=person_id,
                fact_key=fact_key,
                fact_id=str(existing_id),
            )
            return UpsertResult(
                fact_id=existing_id,
                superseded_id=None,
                cap_reached=False,
                skipped=True,
            )
    else:
        # 2. New key. Enforce cap.
        active_count = count_active_facts(cursor, person_id=person_id)
        if active_count >= max_active_facts_per_person:
            log.info(
                "profile_facts.upsert_rejected_cap",
                person_id=person_id,
                fact_key=fact_key,
                active_count=active_count,
                cap=max_active_facts_per_person,
            )
            return UpsertResult(
                fact_id=uuid4(),  # placeholder; never written
                superseded_id=None,
                cap_reached=True,
                skipped=True,
            )

    # 3. Supersede the existing active row, if any.
    new_id = uuid4()
    superseded_id: UUID | None = None
    if existing is not None:
        existing_id = existing[0]
        cursor.execute(
            SUPERSEDE_ACTIVE_FACT,
            {"id": existing_id, "superseded_by": new_id},
        )
        superseded_id = existing_id

    # 4. Insert the new active row.
    cursor.execute(
        INSERT_FACT,
        {
            "id": new_id,
            "person_id": person_id,
            "fact_key": fact_key,
            "question_text": question_text,
            "answer_text": answer_text,
            "source": source,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "prompt_version": prompt_version,
        },
    )

    # 5. Push embedding job. Failure here raises and rolls back the
    #    caller's transaction, leaving the prior active row intact.
    push_embedding(
        record_type="profile_fact",
        record_id=str(new_id),
        source_text=answer_text,
        embedding_model=embedding_model,
        embedding_model_version=embedding_model_version,
    )

    log.info(
        "profile_facts.upserted",
        person_id=person_id,
        fact_key=fact_key,
        fact_id=str(new_id),
        superseded_id=str(superseded_id) if superseded_id else None,
        source=source,
    )
    return UpsertResult(
        fact_id=new_id,
        superseded_id=superseded_id,
        cap_reached=False,
        skipped=False,
    )


# ---------------------------------------------------------------------------
# Async variant for the HTTP edit endpoint
# ---------------------------------------------------------------------------


async def count_active_facts_async(cursor, *, person_id: str) -> int:
    await cursor.execute(COUNT_ACTIVE_FACTS, {"person_id": person_id})
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def upsert_fact_async(
    cursor,
    *,
    person_id: str,
    fact_key: str,
    question_text: str,
    answer_text: str,
    source: str,
    push_embedding: _EmbeddingPusher,
    embedding_model: str,
    embedding_model_version: str,
    max_active_facts_per_person: int = MAX_ACTIVE_FACTS_PER_PERSON,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    prompt_version: str | None = None,
) -> UpsertResult:
    """Async mirror of :func:`upsert_fact` for the FastAPI edit endpoint.

    Caller owns the transaction. The embedding push is sync because
    :class:`EmbeddingJobSender` uses sync boto3 — small enough to call
    inline; matches the pattern in the extraction worker.
    """
    await cursor.execute(
        SELECT_ACTIVE_FACT_BY_KEY,
        {"person_id": person_id, "fact_key": fact_key},
    )
    existing = await cursor.fetchone()

    if existing is not None:
        existing_id, _existing_question, existing_answer, _existing_source = existing
        if existing_answer.strip() == answer_text.strip():
            log.info(
                "profile_facts.upsert_skipped_unchanged",
                person_id=person_id,
                fact_key=fact_key,
                fact_id=str(existing_id),
            )
            return UpsertResult(
                fact_id=existing_id,
                superseded_id=None,
                cap_reached=False,
                skipped=True,
            )
    else:
        active_count = await count_active_facts_async(cursor, person_id=person_id)
        if active_count >= max_active_facts_per_person:
            log.info(
                "profile_facts.upsert_rejected_cap",
                person_id=person_id,
                fact_key=fact_key,
                active_count=active_count,
                cap=max_active_facts_per_person,
            )
            return UpsertResult(
                fact_id=uuid4(),
                superseded_id=None,
                cap_reached=True,
                skipped=True,
            )

    new_id = uuid4()
    superseded_id: UUID | None = None
    if existing is not None:
        existing_id = existing[0]
        await cursor.execute(
            SUPERSEDE_ACTIVE_FACT,
            {"id": existing_id, "superseded_by": new_id},
        )
        superseded_id = existing_id

    await cursor.execute(
        INSERT_FACT,
        {
            "id": new_id,
            "person_id": person_id,
            "fact_key": fact_key,
            "question_text": question_text,
            "answer_text": answer_text,
            "source": source,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "prompt_version": prompt_version,
        },
    )

    push_embedding(
        record_type="profile_fact",
        record_id=str(new_id),
        source_text=answer_text,
        embedding_model=embedding_model,
        embedding_model_version=embedding_model_version,
    )

    log.info(
        "profile_facts.upserted",
        person_id=person_id,
        fact_key=fact_key,
        fact_id=str(new_id),
        superseded_id=str(superseded_id) if superseded_id else None,
        source=source,
    )
    return UpsertResult(
        fact_id=new_id,
        superseded_id=superseded_id,
        cap_reached=False,
        skipped=False,
    )
