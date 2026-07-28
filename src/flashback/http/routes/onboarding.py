"""Archetype onboarding endpoints."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import psycopg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_db_pool,
    get_http_config,
    get_orchestrator,
    get_sqs_client,
)
from flashback.http.models import (
    ArchetypeAnswersRequest,
    ArchetypeAnswersResponse,
    ArchetypeQuestionsResponse,
)
from flashback.llm.interface import Provider
from flashback.onboarding import parse_free_text_answer
from flashback.ground_truth.store import upsert_ground_truth_field
from flashback.onboarding.archetypes import (
    allows_multiple,
    answer_with_label,
    expected_question_ids,
    ground_truth_writes_from_answers,
    merge_implies,
    public_questions_for_relationship,
    render_pronouns,
    resolve_options,
)
from flashback.onboarding.persistence import (
    PersonOnboardingRow,
    fetch_person_onboarding,
    persist_archetype_onboarding,
)
from flashback.orchestrator import OrchestratorProtocol
from flashback.queues import AsyncSQSClient

router = APIRouter(
    prefix="/api/v1/onboarding",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.onboarding")


@router.get("/archetype-questions", response_model=ArchetypeQuestionsResponse)
async def archetype_questions(
    person_id: UUID = Query(...),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> ArchetypeQuestionsResponse:
    """Return 2-3 relationship-tailored tappable questions."""

    person = await _load_person_onboarding_or_http(db_pool, person_id=person_id)
    if person.onboarding_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="onboarding already complete for this person",
        )

    archetype, questions = public_questions_for_relationship(
        person.relationship,
        gender=person.gender,
    )
    return ArchetypeQuestionsResponse(
        person_id=person_id,
        relationship=person.relationship,
        archetype=archetype,
        questions=questions,
    )


@router.post("/archetype-answers", response_model=ArchetypeAnswersResponse)
async def archetype_answers(
    body: ArchetypeAnswersRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    cfg: HttpConfig = Depends(get_http_config),
    sqs: AsyncSQSClient = Depends(get_sqs_client),
    orch: OrchestratorProtocol = Depends(get_orchestrator),
) -> ArchetypeAnswersResponse:
    """Persist archetype answers, then generate the very-first opener.

    This is the only path that ever feeds ``archetype_answers`` to the
    response generator. Subsequent ``/session/start`` calls go through
    the normal opener flow and ignore them.
    """

    person = await _load_person_onboarding_or_http(db_pool, person_id=body.person_id)
    if person.onboarding_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="onboarding already complete for this person",
        )

    answers, implies_blocks = await _resolve_answers(
        cfg=cfg,
        person=person,
        answers=[answer.model_dump(exclude_none=True) for answer in body.answers],
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                locked_person = await fetch_person_onboarding(
                    cur, person_id=body.person_id, for_update=True
                )
                if locked_person is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"person {body.person_id} not found",
                    )
                if locked_person.onboarding_complete:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="onboarding already complete for this person",
                    )
                result = await persist_archetype_onboarding(
                    cur,
                    person=locked_person,
                    answers=answers,
                    implies_blocks=implies_blocks,
                )
                for gt_field, gt_value in ground_truth_writes_from_answers(
                    answers
                ):
                    await upsert_ground_truth_field(
                        cur,
                        body.person_id,
                        field=gt_field,
                        value=gt_value,
                        provenance="onboarding",
                        confidence="high",
                    )

    await _push_entity_embeddings(
        sqs=sqs,
        cfg=cfg,
        jobs=result.embedding_jobs,
    )

    opener_result = await orch.handle_first_time_opener(
        session_id=result.session_id,
        person_id=person.person_id,
        user_id=body.user_id,  # creator's Node user when supplied; NULL = creator era (spec D2)
        session_metadata={
            "archetype_answers": answers,
            "contributor_display_name": body.contributor_display_name or "",
        },
    )

    log.info(
        "onboarding.archetype_completed",
        person_id=str(person.person_id),
        session_id=str(result.session_id),
        new_entities=len(result.embedding_jobs),
        coverage_deltas=result.coverage_deltas,
        opener_length=len(opener_result.opener),
    )
    return ArchetypeAnswersResponse(
        session_id=result.session_id,
        opener=opener_result.opener,
    )


async def _load_person_onboarding_or_http(
    db_pool: AsyncConnectionPool, *, person_id: UUID
) -> PersonOnboardingRow:
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                person = await fetch_person_onboarding(cur, person_id=person_id)
    except psycopg.errors.UndefinedColumn as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="persons onboarding columns are not available",
        ) from exc
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"person {person_id} not found",
        )
    return person


async def _resolve_answers(
    *,
    cfg: HttpConfig,
    person: PersonOnboardingRow,
    answers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_ids = expected_question_ids(person.relationship)
    provided_ids = [str(answer.get("question_id") or "") for answer in answers]
    if set(provided_ids) != expected_ids or len(provided_ids) != len(set(provided_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="answers must include each archetype question exactly once",
        )

    saved_answers: list[dict[str, Any]] = []
    implies_blocks: list[dict[str, Any]] = []
    for raw in answers:
        question_id = str(raw.get("question_id") or "")
        skipped = bool(raw.get("skipped", False))
        option_ids = [
            str(o).strip()
            for o in (raw.get("option_ids") or [])
            if str(o or "").strip()
        ]
        if not option_ids and raw.get("option_id"):
            option_ids = [str(raw["option_id"]).strip()]
        free_text = str(raw.get("free_text") or "").strip()

        if skipped and (option_ids or free_text):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="a skipped answer cannot also carry options or free_text",
            )
        if not skipped and not option_ids and not free_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "each answer must select at least one option, provide "
                    "free_text, or be skipped"
                ),
            )

        try:
            question, options = resolve_options(
                relationship=person.relationship,
                question_id=question_id,
                option_ids=option_ids,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        # Single-choice questions (the ground-truth pair) keep the
        # exactly-one rule: one chip OR free text, never both.
        if not allows_multiple(question) and options and free_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"question_id {question_id!r} accepts exactly one of "
                    "an option or free_text"
                ),
            )

        if skipped:
            saved_answers.append(answer_with_label(question_id=question_id, skipped=True))
            implies_blocks.append({"coverage": [], "entities": []})
            continue

        raw_blocks: list[dict[str, Any]] = [
            option.get("implies") or {} for option in options
        ]
        if free_text:
            raw_blocks.append(
                await parse_free_text_answer(
                    settings=cfg,
                    provider=cast(Provider, cfg.llm_onboarding_parse_provider),
                    model=cfg.llm_onboarding_parse_model,
                    timeout=cfg.llm_onboarding_parse_timeout_seconds,
                    max_tokens=cfg.llm_onboarding_parse_max_tokens,
                    relationship=person.relationship,
                    question_text=str(question["text"]),
                    free_text=free_text,
                )
            )

        saved_answers.append(
            answer_with_label(
                question_id=question_id,
                option_ids=[str(option["id"]) for option in options],
                labels=[
                    render_pronouns(str(option["label"]), person.gender)
                    for option in options
                ],
                free_text=free_text or None,
            )
        )
        implies_blocks.append(merge_implies(raw_blocks))

    return saved_answers, implies_blocks


async def _push_entity_embeddings(
    *,
    sqs: AsyncSQSClient,
    cfg: HttpConfig,
    jobs,
) -> None:
    if not jobs:
        return
    if not cfg.embedding_queue_url:
        log.warning(
            "onboarding.embedding_skipped",
            reason="embedding_queue_url_not_configured",
            count=len(jobs),
        )
        return
    for job in jobs:
        await sqs.send_message(
            cfg.embedding_queue_url,
            {
                "record_type": "entity",
                "record_id": job.entity_id,
                "source_text": job.source_text,
                "embedding_model": cfg.embedding_model,
                "embedding_model_version": cfg.embedding_model_version,
            },
        )
