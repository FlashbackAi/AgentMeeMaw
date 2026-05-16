"""Themes HTTP surface.

Two endpoints in v1:

* ``POST /themes/{theme_id}/unlock_prepare`` — return the archetype
  questions for a locked theme. If they haven't been generated yet
  (universals start with ``archetype_questions = NULL``), call the
  small LLM, persist on the row, and return. The theme stays in
  ``state='locked'`` — unlock itself happens at the next ``/session/start``
  call carrying ``theme_id`` + ``archetype_answers`` in session_metadata.

* ``GET /themes/{theme_id}`` — return a theme's current row shape.
  Mostly useful for unit tests and as a thin debug surface; Node reads
  the user-facing list directly from ``active_themes_with_tier``.

The unlock_complete transition lives on ``/session/start``: when the
caller passes ``theme_id`` and ``archetype_answers`` in session_metadata,
the orchestrator flips the theme to ``unlocked`` inside its bootstrap
flow.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_http_config
from flashback.themes.archetype_llm import (
    ARCHETYPE_PROMPT_VERSION,
    ArchetypeContextMoment,
    ArchetypeQuestion,
    generate_archetype_questions,
)
from flashback.themes.repository import (
    fetch_theme_by_id_async,
    update_archetype_questions_async,
)
from flashback.themes.universal import get_universal_theme

router = APIRouter(prefix="/themes", dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.themes")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class UnlockPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID


class ArchetypeOption(BaseModel):
    option_id: str
    label: str


class ArchetypeQuestionPayload(BaseModel):
    question_id: str
    text: str
    options: list[ArchetypeOption]
    allow_skip: bool = True
    allow_free_text: bool = True


class UnlockPrepareResponse(BaseModel):
    theme_id: UUID
    person_id: UUID
    slug: str
    display_name: str
    kind: str
    state: str
    archetype_questions: list[ArchetypeQuestionPayload]
    prompt_version: str = ARCHETYPE_PROMPT_VERSION
    generated_this_call: bool = Field(
        default=False,
        description=(
            "True if the LLM was called as part of this request; "
            "False when cached questions were returned unchanged."
        ),
    )


class ThemeResponse(BaseModel):
    theme_id: UUID
    person_id: UUID
    slug: str
    display_name: str
    kind: str
    state: str
    description: str | None
    archetype_questions: list[dict] | None
    archetype_answers: list[dict] | None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{theme_id}/unlock_prepare",
    response_model=UnlockPrepareResponse,
)
async def unlock_prepare(
    theme_id: UUID,
    body: UnlockPrepareRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    cfg: HttpConfig = Depends(get_http_config),
) -> UnlockPrepareResponse:
    """Return archetype questions for a locked theme. Generates + caches
    on first call; returns cached payload on subsequent calls."""
    structlog.contextvars.bind_contextvars(
        theme_id=str(theme_id),
        person_id=str(body.person_id),
    )

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            theme = await fetch_theme_by_id_async(
                cur,
                theme_id=str(theme_id),
                person_id=str(body.person_id),
            )
            if theme is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="theme not found for this person",
                )
            subject_name = await _fetch_subject_name(cur, theme.person_id)
            if subject_name is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="subject not found",
                )

    generated_this_call = False
    questions: list[ArchetypeQuestion]
    if theme.archetype_questions:
        questions = _rehydrate_archetype_questions(theme.archetype_questions)
    else:
        description = theme.description
        if not description and theme.kind == "universal":
            universal = get_universal_theme(theme.slug)
            description = (
                universal.description if universal is not None else theme.display_name
            )
        if not description:
            description = theme.display_name

        questions = await generate_archetype_questions(
            settings=cfg,
            theme_slug=theme.slug,
            theme_display_name=theme.display_name,
            theme_description=description,
            theme_kind=theme.kind,
            subject_name=subject_name,
            subject_relationship=None,
            context_moments=None,
        )
        if questions:
            payload = [q.to_payload() for q in questions]
            async with db_pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        await update_archetype_questions_async(
                            cur,
                            theme_id=str(theme_id),
                            questions=payload,
                        )
            generated_this_call = True

    log.info(
        "themes.unlock_prepare",
        theme_id=str(theme_id),
        slug=theme.slug,
        kind=theme.kind,
        questions_count=len(questions),
        generated_this_call=generated_this_call,
    )

    return UnlockPrepareResponse(
        theme_id=theme_id,
        person_id=body.person_id,
        slug=theme.slug,
        display_name=theme.display_name,
        kind=theme.kind,
        state=theme.state,
        archetype_questions=[
            ArchetypeQuestionPayload(
                question_id=q.question_id,
                text=q.text,
                options=[
                    ArchetypeOption(
                        option_id=o["option_id"],
                        label=o["label"],
                    )
                    for o in q.options
                ],
                allow_skip=q.allow_skip,
                allow_free_text=q.allow_free_text,
            )
            for q in questions
        ],
        generated_this_call=generated_this_call,
    )


@router.get("/{theme_id}", response_model=ThemeResponse)
async def get_theme(
    theme_id: UUID,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> ThemeResponse:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            theme = await fetch_theme_by_id_async(
                cur, theme_id=str(theme_id)
            )
    if theme is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="theme not found",
        )
    return ThemeResponse(
        theme_id=UUID(theme.id),
        person_id=UUID(theme.person_id),
        slug=theme.slug,
        display_name=theme.display_name,
        kind=theme.kind,
        state=theme.state,
        description=theme.description,
        archetype_questions=theme.archetype_questions,
        archetype_answers=theme.archetype_answers,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_subject_name(cur, person_id: str) -> str | None:
    await cur.execute("SELECT name FROM persons WHERE id = %s", (person_id,))
    row = await cur.fetchone()
    return row[0] if row is not None else None


def _rehydrate_archetype_questions(
    raw: list[dict],
) -> list[ArchetypeQuestion]:
    out: list[ArchetypeQuestion] = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        out.append(
            ArchetypeQuestion(
                question_id=str(q.get("question_id") or ""),
                text=str(q.get("text") or ""),
                options=[
                    {
                        "option_id": str(o.get("option_id") or ""),
                        "label": str(o.get("label") or ""),
                    }
                    for o in (q.get("options") or [])
                    if isinstance(o, dict)
                ],
                allow_skip=bool(q.get("allow_skip", True)),
                allow_free_text=bool(q.get("allow_free_text", True)),
            )
        )
    return out
