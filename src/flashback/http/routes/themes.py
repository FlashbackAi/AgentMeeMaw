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

from datetime import datetime, timezone

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
    upsert_archetype_draft_async,
)
from flashback.themes.universal import get_universal_theme
from flashback.tribute.composer import compose_directives
from flashback.tribute.config_repository import (
    active_featured_campaign_db,
    fetch_profile_by_group,
    resolve_campaign_db,
)
from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    bank_to_archetype_questions,
    campaign_applies,
)
from flashback.tribute.relationships import ensure_relationship_group
from flashback.tribute.theme import (
    TRIBUTE_ARCHETYPE_MAX,
    TRIBUTE_ARCHETYPE_MIN,
)

router = APIRouter(prefix="/themes", dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.themes")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class UnlockPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    # Campaign skin slug the UI is featuring (tribute themes only). Absent ->
    # the date-windowed featured campaign, else neutral.
    campaign: str | None = Field(None, max_length=64)


class ArchetypeOption(BaseModel):
    option_id: str
    label: str


class ArchetypeQuestionPayload(BaseModel):
    question_id: str
    text: str
    options: list[ArchetypeOption]
    allow_skip: bool = True
    allow_free_text: bool = True
    allow_multiple: bool = True


class UnlockPrepareResponse(BaseModel):
    theme_id: UUID
    person_id: UUID
    slug: str
    display_name: str
    kind: str
    state: str
    archetype_questions: list[ArchetypeQuestionPayload]
    archetype_answers_draft: list[dict] | None = Field(
        default=None,
        description=(
            "Mid-flow partial answers persisted via "
            "POST /themes/{id}/archetype_progress. Frontend uses this "
            "to restore chip selections / free-text on resume."
        ),
    )
    prompt_version: str = ARCHETYPE_PROMPT_VERSION
    generated_this_call: bool = Field(
        default=False,
        description=(
            "True if the LLM was called as part of this request; "
            "False when cached questions were returned unchanged."
        ),
    )


class ArchetypeAnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    # Multi-select shape; legacy single option_id/option_label stays
    # accepted. Chips and free_text may combine on one answer.
    option_ids: list[str] | None = None
    option_labels: list[str] | None = None
    option_id: str | None = None
    option_label: str | None = None
    free_text: str | None = None
    skipped: bool = False


class ArchetypeProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    answers: list[ArchetypeAnswerInput]


class ArchetypeProgressResponse(BaseModel):
    saved: bool = True
    answered: int
    total: int


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
    archetype_answers_draft: list[dict] | None


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
    elif theme.kind == "tribute":
        # Tribute CRM chain (spec 2026-07-14 §6.3): campaign bank override ->
        # relationship-profile bank -> LLM generation seeded with relationship
        # + occasion context. Ephemeral priors only (invariant #22).
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                campaign = await resolve_campaign_db(cur, body.campaign)
                if not campaign.id and not body.campaign:
                    featured = await active_featured_campaign_db(
                        cur, datetime.now(timezone.utc).date()
                    )
                    if featured is not None:
                        campaign = featured
                group = await ensure_relationship_group(
                    cur, settings=cfg, person_id=str(body.person_id)
                )
                # Relationship targeting (migration 0041): a campaign scoped
                # to specific groups never skins another relationship's card
                # — and its bank override never replaces that profile's
                # questions.
                if not campaign_applies(campaign, group):
                    campaign = NEUTRAL_CAMPAIGN
                profile = await fetch_profile_by_group(cur, group)
                if profile is None and group != "other":
                    profile = await fetch_profile_by_group(cur, "other")
                await cur.execute(
                    "SELECT relationship FROM persons WHERE id = %s",
                    (str(body.person_id),),
                )
                rel_row = await cur.fetchone()
        subject_relationship = rel_row[0] if rel_row else None
        bank = None
        if profile is not None:
            bank = compose_directives(profile, campaign).bank
        if bank:
            questions = bank_to_archetype_questions(bank)
        else:
            questions = await generate_archetype_questions(
                settings=cfg,
                theme_slug=theme.slug,
                theme_display_name=theme.display_name,
                theme_description=theme.description or theme.display_name,
                theme_kind=theme.kind,
                subject_name=subject_name,
                subject_relationship=subject_relationship,
                context_moments=None,
                min_questions=TRIBUTE_ARCHETYPE_MIN,
                max_questions=TRIBUTE_ARCHETYPE_MAX,
                extra_context=campaign.archetype_extra_context,
            )
    else:
        description = theme.description
        if not description and theme.kind == "universal":
            universal = get_universal_theme(theme.slug)
            description = (
                universal.description
                if universal is not None
                else theme.display_name
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
            min_questions=3,
            max_questions=4,
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
                allow_multiple=q.allow_multiple,
            )
            for q in questions
        ],
        archetype_answers_draft=theme.archetype_answers_draft,
        generated_this_call=generated_this_call,
    )


@router.post(
    "/{theme_id}/archetype_progress",
    response_model=ArchetypeProgressResponse,
)
async def archetype_progress(
    theme_id: UUID,
    body: ArchetypeProgressRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> ArchetypeProgressResponse:
    """Persist partial archetype answers so the user can resume.

    Last-write-wins: the supplied ``answers`` array replaces the
    current draft in full. The frontend sends the complete current
    state on every chip tap. Rejects if the theme is already unlocked
    (409) — no point drafting on a committed theme.
    """
    structlog.contextvars.bind_contextvars(
        theme_id=str(theme_id),
        person_id=str(body.person_id),
    )

    answers_payload = [a.model_dump(exclude_none=False) for a in body.answers]

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
            if theme.state == "unlocked":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="theme is already unlocked; archetype progress is locked-only",
                )
        async with conn.transaction():
            async with conn.cursor() as cur:
                updated = await upsert_archetype_draft_async(
                    cur,
                    theme_id=str(theme_id),
                    person_id=str(body.person_id),
                    answers=answers_payload,
                )

    if not updated:
        # Race: theme flipped to unlocked between the check above and the
        # UPDATE. Treat as 409 — the draft would be orphaned.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="theme is no longer locked; archetype progress rejected",
        )

    total = (
        len(theme.archetype_questions) if theme.archetype_questions else 0
    )
    answered = sum(
        1
        for a in answers_payload
        if a.get("option_ids")
        or a.get("option_id")
        or a.get("free_text")
        or a.get("skipped")
    )

    log.info(
        "themes.archetype_progress",
        theme_id=str(theme_id),
        slug=theme.slug,
        answered=answered,
        total=total,
    )

    return ArchetypeProgressResponse(
        saved=True,
        answered=answered,
        total=total,
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
        archetype_answers_draft=theme.archetype_answers_draft,
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
                allow_multiple=bool(q.get("allow_multiple", True)),
            )
        )
    return out
