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
from flashback.tribute.repository import (
    fetch_latest_tribute_answers_async,
    fetch_open_tribute_id_async,
    fetch_tribute_archetype_answers_async,
)
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
    tribute_answered: list[dict] | None = Field(
        default=None,
        description=(
            "Tribute themes only: archetype answers already committed for this "
            "OCCASION, used to prefill/skip. Resolved slug-scoped (survives "
            "campaign version bumps, 2026-07-22) with precedence: the open "
            "tribute row's answers win; the theme-level answers (pre-0042) "
            "fill in only when the row has none."
        ),
    )
    archetype_complete: bool = Field(
        default=False,
        description=(
            "Every served archetype question is already covered by "
            "tribute_answered (normalized question_text match). Convenience / "
            "telemetry — next_step is the routing contract."
        ),
    )
    next_step: str = Field(
        default="archetype",
        description=(
            "Where 'Keep going' should land AFTER unlock_prepare: 'archetype' "
            "(questions remain), 'message' (campaign only, archetype done + "
            "message slot empty), or 'conversation' (everything else, incl. "
            "every standalone entry — message is campaign-only). The frontend's "
            "tribute-status branches (watch/rendering/none) outrank this."
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
    # The question wording. It's the STABLE match key for re-ask suppression
    # (ids differ across campaign versions; text identifies a repeated ask).
    # Carry it on every answer -- including skips -- so a skipped question
    # lands in tribute_answered and is never re-asked (2026-07-27). Optional
    # for back-compat; the commit path (session_metadata) already sends it.
    question_text: str | None = None
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
    tribute_answered: list[dict] | None = None
    questions: list[ArchetypeQuestion]
    # next_step inputs (2026-07-22): whether this entry is a real campaign (so
    # the message step is even allowed — standalone/neutral never is) and
    # whether the campaign's tribute already has its message.
    is_campaign = False
    message_present = False
    if theme.kind == "tribute":
        # Tribute CRM chain (spec 2026-07-14 §6.3): campaign bank override ->
        # relationship-profile bank -> theme-row cache (pre-CRM legacies) ->
        # LLM generation. The authored bank beats the cache: a legacy that
        # ran an earlier campaign must still receive a NEW campaign's
        # questions (prod 2026-07-16: the FD legacy's cached 4 questions
        # were shadowing every future bank). Ephemeral priors only
        # (invariant #22).
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
                # A real, applicable campaign resolved => campaign entry (the
                # only kind where next_step may be "message"). Neutral => no id.
                is_campaign = bool(campaign.id)
                profile = await fetch_profile_by_group(cur, group)
                if profile is None and group != "other":
                    profile = await fetch_profile_by_group(cur, "other")
                await cur.execute(
                    "SELECT relationship FROM persons WHERE id = %s",
                    (str(body.person_id),),
                )
                rel_row = await cur.fetchone()
                # Per-campaign answers (0042): what THIS campaign's open
                # tribute has already committed — the app prefills matching
                # questions and can skip the modal when nothing is new.
                # No open tribute (last one completed)? Fall back to the
                # latest same-campaign tribute so re-entry prefills instead
                # of starting blank.
                # Scope by SLUG, not the version-specific campaign id, so
                # answers survive CRM campaign edits (2026-07-22 bug: a version
                # bump orphaned the tribute and re-asked answered questions).
                open_id = await fetch_open_tribute_id_async(
                    cur,
                    person_id=str(body.person_id),
                    theme_id=str(theme_id),
                    campaign_slug=campaign.slug or None,
                )
                if open_id is not None:
                    tribute_answered = (
                        await fetch_tribute_archetype_answers_async(
                            cur, tribute_id=open_id
                        )
                    )
                else:
                    tribute_answered = (
                        await fetch_latest_tribute_answers_async(
                            cur,
                            person_id=str(body.person_id),
                            theme_id=str(theme_id),
                            campaign_slug=campaign.slug or None,
                        )
                    )
                # Fold theme-level answers in as a FALLBACK (frontend's call,
                # 2026-07-22): tribute-row answers win when present; the
                # pre-0042 theme-level answers fill in only when the row has
                # none, so legacy tributes stop re-asking with zero FE change.
                if not tribute_answered and isinstance(
                    theme.archetype_answers, list
                ):
                    tribute_answered = theme.archetype_answers
                # Message-slot state for next_step: the campaign's open tribute
                # already has its "one thing to say"?
                if open_id is not None:
                    await cur.execute(
                        "SELECT message_text IS NOT NULL "
                        "AND length(btrim(message_text)) > 0 "
                        "FROM tributes WHERE id = %s",
                        (open_id,),
                    )
                    mrow = await cur.fetchone()
                    message_present = bool(mrow[0]) if mrow else False
        subject_relationship = rel_row[0] if rel_row else None
        bank = None
        if profile is not None:
            bank = compose_directives(profile, campaign).bank
        if bank:
            questions = bank_to_archetype_questions(bank)
        elif theme.archetype_questions:
            questions = _rehydrate_archetype_questions(
                theme.archetype_questions
            )
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
    elif theme.archetype_questions:
        questions = _rehydrate_archetype_questions(theme.archetype_questions)
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

    # ── next_step: the server's routing opinion AFTER unlock_prepare (2026-07-22).
    # The frontend's tribute-status branches (complete->watch, generating->
    # rendering, no theme->none) run BEFORE this and outrank it; next_step only
    # governs where "Keep going" lands once unlock_prepare is actually called.
    # Coverage match mirrors the frontend: normalized (trim/collapse/lower)
    # question_text against the (slug-scoped, theme-folded) answered set.
    def _norm(s: str) -> str:
        return " ".join((s or "").split()).lower()

    _answered_texts = {
        _norm(a.get("question_text", ""))
        for a in (tribute_answered or [])
        if isinstance(a, dict)
    }
    archetype_complete = all(_norm(q.text) in _answered_texts for q in questions)
    if not archetype_complete:
        next_step = "archetype"
    elif is_campaign and not message_present:
        # message step is campaign-only (0050: /message 400s for standalone),
        # so a neutral/standalone entry never routes here.
        next_step = "message"
    else:
        next_step = "conversation"

    log.info(
        "themes.unlock_prepare",
        theme_id=str(theme_id),
        slug=theme.slug,
        kind=theme.kind,
        questions_count=len(questions),
        archetype_complete=archetype_complete,
        next_step=next_step,
        generated_this_call=generated_this_call,
    )

    return UnlockPrepareResponse(
        archetype_complete=archetype_complete,
        next_step=next_step,
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
        tribute_answered=tribute_answered,
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
