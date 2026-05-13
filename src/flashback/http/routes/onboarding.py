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
from flashback.onboarding.archetypes import (
    answer_with_label,
    expected_question_ids,
    public_questions_for_relationship,
    resolve_answer,
    sanitize_implies,
)
from flashback.onboarding.persistence import (
    RoleRow,
    fetch_role,
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
    role_id: UUID = Query(...),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> ArchetypeQuestionsResponse:
    """Return 2-3 relationship-tailored tappable questions."""

    role = await _load_role_or_http(db_pool, role_id=role_id)
    if role.onboarding_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="onboarding already complete for this role",
        )

    archetype, questions = public_questions_for_relationship(role.relationship)
    return ArchetypeQuestionsResponse(
        role_id=role_id,
        relationship=role.relationship,
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

    role = await _load_role_or_http(db_pool, role_id=body.role_id)
    if role.onboarding_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="onboarding already complete for this role",
        )

    answers, implies_blocks = await _resolve_answers(
        cfg=cfg,
        role=role,
        answers=[answer.model_dump(exclude_none=True) for answer in body.answers],
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                locked_role = await fetch_role(cur, role_id=body.role_id, for_update=True)
                if locked_role is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"role {body.role_id} not found",
                    )
                if locked_role.onboarding_complete:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="onboarding already complete for this role",
                    )
                result = await persist_archetype_onboarding(
                    cur,
                    role=locked_role,
                    answers=answers,
                    implies_blocks=implies_blocks,
                )

    await _push_entity_embeddings(
        sqs=sqs,
        cfg=cfg,
        jobs=result.embedding_jobs,
    )

    opener_result = await orch.handle_first_time_opener(
        session_id=result.session_id,
        person_id=role.person_id,
        role_id=body.role_id,
        session_metadata={
            "archetype_answers": answers,
            "contributor_display_name": body.contributor_display_name or "",
        },
    )

    log.info(
        "onboarding.archetype_completed",
        role_id=str(body.role_id),
        person_id=str(role.person_id),
        session_id=str(result.session_id),
        new_entities=len(result.embedding_jobs),
        coverage_deltas=result.coverage_deltas,
        opener_length=len(opener_result.opener),
    )
    return ArchetypeAnswersResponse(
        session_id=result.session_id,
        opener=opener_result.opener,
    )


async def _load_role_or_http(
    db_pool: AsyncConnectionPool, *, role_id: UUID
) -> RoleRow:
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                role = await fetch_role(cur, role_id=role_id)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="person_roles onboarding columns are not available",
        ) from exc
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"role {role_id} not found",
        )
    return role


async def _resolve_answers(
    *,
    cfg: HttpConfig,
    role: RoleRow,
    answers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_ids = expected_question_ids(role.relationship)
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
        option_id = raw.get("option_id")
        free_text = str(raw.get("free_text") or "").strip()

        selected_count = int(bool(skipped)) + int(bool(option_id)) + int(bool(free_text))
        if selected_count != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "each answer must choose exactly one of option_id, "
                    "free_text, or skipped"
                ),
            )

        try:
            question, option = resolve_answer(
                relationship=role.relationship,
                question_id=question_id,
                option_id=str(option_id) if option_id else None,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        if skipped:
            saved_answers.append(answer_with_label(question_id=question_id, skipped=True))
            implies_blocks.append({"coverage": [], "entities": []})
            continue

        if option is not None:
            saved_answers.append(
                answer_with_label(
                    question_id=question_id,
                    option_id=str(option_id),
                    label=str(option["label"]),
                )
            )
            implies_blocks.append(sanitize_implies(option.get("implies")))
            continue

        implies = await parse_free_text_answer(
            settings=cfg,
            provider=cast(Provider, cfg.llm_onboarding_parse_provider),
            model=cfg.llm_onboarding_parse_model,
            timeout=cfg.llm_onboarding_parse_timeout_seconds,
            max_tokens=cfg.llm_onboarding_parse_max_tokens,
            relationship=role.relationship,
            question_text=str(question["text"]),
            free_text=free_text,
        )
        saved_answers.append(
            answer_with_label(question_id=question_id, free_text=free_text)
        )
        implies_blocks.append(implies)

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
