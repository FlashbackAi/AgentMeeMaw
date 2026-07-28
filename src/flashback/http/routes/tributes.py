"""Tribute generation endpoint.

POST /tributes/{id}/generate gates on the tribute_status view, assembles a
script, composes the artifact-kind context, writes it (keyed) to the
tribute row, flips status to 'generating', and pushes a trigger-only
artifact_generation job. Node's compiled renderer reads the context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg_pool import AsyncConnectionPool

from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_artifact_generation_queue,
    get_db_pool,
    get_http_config,
    get_tribute_render_queue,
)
from flashback.http.models import (
    TributeCampaignOut,
    TributeCampaignsResponse,
    TributeEditRequest,
    TributeEditSuggestion,
    TributeEditSuggestionsRequest,
    TributeEditSuggestionsResponse,
    TributeGenerateRequest,
    TributeGenerateResponse,
    TributeMessageRequest,
    TributeProgressResponse,
    TributeRegenerateRequest,
)
from flashback.tribute.composer import ComposedDirectives, compose_directives
from flashback.tribute.opener_presets import public_catalog as opener_public_catalog
from flashback.tribute.config_repository import (
    active_featured_campaign_db,
    fetch_campaign_by_id,
    fetch_campaign_by_slug,
    fetch_profile_by_group,
    fetch_visual_theme_by_id,
    list_rows,
    resolve_campaign_db,
)
from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    CampaignConfig,
    VisualThemeConfig,
    campaign_applies,
)
from flashback.tribute.progress import (
    fetch_tribute_progress_async,
    progress_to_payload,
)
from flashback.tribute.invitation import resolve_invitation_copy
from flashback.tribute.message_capture import polish_and_store_message
from flashback.tribute.relationships import ensure_relationship_group
from flashback.tribute.leads import build_leads, leads_to_lines
from flashback.tribute.repository import (
    fetch_render_archetype_answers_async,
    fetch_scene_moments_async,
    fetch_theme_scene_moments_async,
    fetch_tribute_campaign_id_async,
    fetch_tribute_for_assembly_async,
    fetch_tribute_generation_context_async,
    set_status_async,
    stamp_tribute_campaign_async,
    write_tribute_generation_context_async,
)
from flashback.tribute_video.context import (
    CONTEXT_KEY,
    build_context_dict,
    choose_candidate_pool,
    order_candidates_for_narrative,
)
from flashback.tribute_video.sequencer import (
    LAYOUT_CATALOG,
    MOTION_PRESETS,
    PINNABLE_ROLES,
)
from flashback.tribute_video.edit_suggestions import generate_edit_suggestions
from flashback.tribute.checklist import MEMORIES_TARGET
from flashback.tribute.theme import STORYBOOK_MAX_PAGES

if TYPE_CHECKING:
    from flashback.config import HttpConfig
    from flashback.queues.artifact_generation import (
        ArtifactGenerationQueueProducer,
    )
    from flashback.queues.tribute_render import TributeRenderQueueProducer

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.tributes")


async def _resolve_render_config(
    cur,
    *,
    person_id: str,
    tribute_id: UUID | str | None = None,
    campaign_slug: str | None = None,
    campaign_id_hint: str | None = None,
    settings=None,
):
    """Resolve (campaign, profile, directives, visual_theme) for a render.

    Campaign: the tribute row's stamped campaign_id wins, else the campaign
    the prior render snapshot pinned (``campaign_id_hint``), else the slug
    the caller passed, else neutral. A stamped/pinned campaign is ALWAYS
    freshened to its slug's current published version: completed tributes
    keep their frozen row id on purpose (snapshots), but re-resolution must
    see live config — "regenerate picks up CRM edits" is the recovery path
    (prod 2026-07-16: a theme swap on the campaign didn't reach regenerated
    videos because the stamped OLD campaign row still carried the old
    theme id). Profile: the person's cached relationship group (resolved
    lazily when settings are provided), safety-floored on 'other'. Never
    raises — a render never blocks on config (spec §6.5).
    """
    campaign = NEUTRAL_CAMPAIGN
    profile = None
    directives: ComposedDirectives | None = None
    visual_theme: VisualThemeConfig | None = None
    try:
        if tribute_id is not None:
            campaign_id = await fetch_tribute_campaign_id_async(
                cur, tribute_id=tribute_id
            )
            if campaign_id:
                campaign = await fetch_campaign_by_id(cur, campaign_id) or (
                    NEUTRAL_CAMPAIGN
                )
        if not campaign.id and campaign_id_hint:
            # Prod 2026-07-16 (morning): regenerating an unstamped tribute
            # reverted to neutral — the campaign only ever existed in the
            # /generate body and the snapshot. Follow the snapshot's pin.
            pinned = await fetch_campaign_by_id(cur, campaign_id_hint)
            if pinned is not None:
                campaign = pinned
        if campaign.id:
            # Follow supersession to the live published version (see
            # docstring). Keep the pinned row when nothing published holds
            # the slug anymore (campaign archived) — never block a render.
            fresh = await fetch_campaign_by_slug(cur, campaign.slug)
            if fresh is not None:
                campaign = fresh
        if not campaign.id:
            campaign = await resolve_campaign_db(cur, campaign_slug)

        if settings is not None:
            group = await ensure_relationship_group(
                cur, settings=settings, person_id=person_id
            )
        else:
            await cur.execute(
                "SELECT relationship_group FROM persons WHERE id = %s",
                (person_id,),
            )
            row = await cur.fetchone()
            group = (row[0] if row else None) or "other"

        # Relationship targeting (0041): a campaign scoped to other groups
        # never styles this person's render — degrade to neutral, the
        # profile still owns tone/theme.
        if not campaign_applies(campaign, group):
            campaign = NEUTRAL_CAMPAIGN

        profile = await fetch_profile_by_group(cur, group)
        if profile is None and group != "other":
            profile = await fetch_profile_by_group(cur, "other")
        if profile is not None:
            directives = compose_directives(profile, campaign)
            if directives.visual_theme_id:
                visual_theme = await fetch_visual_theme_by_id(
                    cur, directives.visual_theme_id
                )
                if visual_theme is not None and visual_theme.state != "published":
                    visual_theme = None
    except Exception:
        log.warning("tribute.config_resolution_failed",
                    person_id=person_id, exc_info=True)
    return campaign, profile, directives, visual_theme


def _style_dict(visual_theme: VisualThemeConfig | None) -> dict | None:
    if visual_theme is None:
        return None
    return {
        "visual_theme_id": visual_theme.id,
        "fonts": visual_theme.fonts,
        "ink": visual_theme.ink,
        "audio_slug": visual_theme.audio_slug,
        # Remotion composition recipe (migration 0044). The render worker reads
        # these via recipe_kwargs_from_style; empty values fall back to the
        # code-side Friendship default (a render never blocks on config).
        "recipe": {
            "layout_palette": list(visual_theme.layout_palette or []),
            "layout_pins": visual_theme.layout_pins or {},
            "pacing": visual_theme.pacing or {},
            "motion_preset": visual_theme.motion_preset or "",
            # Engine pin (0045): '' = worker default. Lets an occasion keep
            # the legacy look (Father's Day) while others render as Flashbacks.
            "render_engine": visual_theme.render_engine or "",
        },
    }


@router.get("/flashback/layouts")
async def flashback_layouts() -> dict:
    """The layout library + motion presets for the CRM recipe picker.

    The agent<->Node contract behind the visual-theme palette/pins/motion
    controls (see docs/FLASHBACK_NODE_PROMPT.md §3b). Read-only; static.
    """
    return {
        "layouts": LAYOUT_CATALOG,
        "motion_presets": MOTION_PRESETS,
        "pinnable_roles": PINNABLE_ROLES,
    }


@router.get("/flashback/opener-presets")
async def flashback_opener_presets() -> dict:
    """The opening-style catalog for the CRM opener dropdown (profile editor).

    The admin picks a slug, stored on the profile's opener.preset; the composer
    resolves it to a style + examples at compose time. Read-only; static.
    """
    return {"opener_presets": opener_public_catalog()}


@router.get(
    "/tributes/{tribute_id}/progress", response_model=TributeProgressResponse
)
async def get_tribute_progress(
    tribute_id: UUID,
    person_id: UUID = Query(..., description="Owning legacy; scopes the lookup."),
    campaign: str | None = Query(
        None,
        description=(
            "Optional campaign skin slug. When set, the title and the "
            "message slot's hint use the skin copy; otherwise neutral."
        ),
    ),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> TributeProgressResponse:
    """Standalone read of the tribute completion meter.

    The same decorated shape /turn emits as `tribute_progress`, but pollable
    on its own so the meter updates without a chat turn. Owner-scoped: a
    tribute that doesn't belong to ``person_id`` 404s. Pure read, no
    side effects -- render status (video/PDF URLs) is a separate concern
    Node reads from the tribute_status view directly.
    """
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            # The stamped entry campaign wins; the query param stays accepted
            # as the fallback for pre-0039 tributes.
            resolved_campaign = None
            stamped_id = await fetch_tribute_campaign_id_async(
                cur, tribute_id=tribute_id
            )
            if stamped_id:
                resolved_campaign = await fetch_campaign_by_id(cur, stamped_id)
            if resolved_campaign is None:
                resolved_campaign = await resolve_campaign_db(cur, campaign or None)
            hint = await resolve_invitation_copy(
                cur,
                tribute_id=str(tribute_id),
                person_id=str(person_id),
                wm_campaign_slug=campaign or None,
            )
            progress = await fetch_tribute_progress_async(
                cur,
                tribute_id=tribute_id,
                campaign=resolved_campaign,
                person_id=person_id,
                message_hint_override=hint,
            )
    if progress is None:
        raise HTTPException(status_code=404, detail="tribute not found")
    return TributeProgressResponse(**progress_to_payload(progress))


@router.post(
    "/tributes/{tribute_id}/message", response_model=TributeProgressResponse
)
async def submit_tribute_message(
    tribute_id: UUID,
    body: TributeMessageRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    settings: "HttpConfig" = Depends(get_http_config),
) -> TributeProgressResponse:
    """Capture the tribute message directly from the tribute card — no chat.

    The finish-without-chat lane (design 2026-07-15): when the message is
    the only unfilled slot, Node shows the resolved invitation question on
    the tribute card and POSTs the answer here. The text is polished by the
    same small LLM as the in-chat lane, written to the row (re-answering
    before generate simply replaces it), and the fresh progress comes back
    so the card can flip to 100% + Generate in one round trip.
    """
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT person_id::text, status, campaign_id FROM tributes WHERE id = %s",
                (str(tribute_id),),
            )
            row = await cur.fetchone()
    if row is None or row[0] != str(body.person_id):
        raise HTTPException(status_code=404, detail="tribute not found")
    if row[1] in ("complete", "superseded"):
        raise HTTPException(
            status_code=409,
            detail=(
                "tribute already rendered; edit the message via a new "
                "tribute or /regenerate flows"
            ),
        )
    # The message slot is campaign-only (two-meter model, design 2026-07-22).
    # The standalone keepsake has no message; reject the write rather than
    # silently create one that its meter ignores.
    if row[2] is None:
        raise HTTPException(
            status_code=409,
            detail="standalone tribute has no message step (campaign-only)",
        )

    await polish_and_store_message(
        person_id=body.person_id,
        tribute_id=str(tribute_id),
        raw=body.text,
        db_pool=db_pool,
        settings=settings,
        source="tribute_card",
    )

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            campaign = None
            stamped_id = await fetch_tribute_campaign_id_async(
                cur, tribute_id=tribute_id
            )
            if stamped_id:
                campaign = await fetch_campaign_by_id(cur, stamped_id)
            if campaign is None:
                campaign = await resolve_campaign_db(cur, None)
            hint = await resolve_invitation_copy(
                cur,
                tribute_id=str(tribute_id),
                person_id=str(body.person_id),
            )
            progress = await fetch_tribute_progress_async(
                cur,
                tribute_id=tribute_id,
                campaign=campaign,
                person_id=body.person_id,
                message_hint_override=hint,
            )
    if progress is None:
        raise HTTPException(status_code=404, detail="tribute status unavailable")
    return TributeProgressResponse(**progress_to_payload(progress))


@router.post("/tributes/{tribute_id}/generate", response_model=TributeGenerateResponse)
async def generate_tribute(
    tribute_id: UUID,
    body: TributeGenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
    tribute_render_queue: "TributeRenderQueueProducer | None" = Depends(
        get_tribute_render_queue
    ),
    settings: "HttpConfig" = Depends(get_http_config),
) -> TributeGenerateResponse:
    # 1) Gate + ownership via the status view + the tribute row.
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            tribute = await fetch_tribute_for_assembly_async(cur, tribute_id=tribute_id)
            if tribute is None or tribute["person_id"] != str(body.person_id):
                raise HTTPException(status_code=404, detail="tribute not found")
            progress = await fetch_tribute_progress_async(cur, tribute_id=tribute_id)
    ground_truth = await fetch_ground_truth(db_pool, body.person_id)

    if progress is None:
        raise HTTPException(status_code=404, detail="tribute status unavailable")

    if body.artifact_kind == "tribute_video":
        return await _generate_video(
            tribute_id=tribute_id,
            body=body,
            db_pool=db_pool,
            tribute=tribute,
            progress=progress,
            ground_truth=ground_truth,
            tribute_render_queue=tribute_render_queue,
            settings=settings,
        )

    # The tribute STORYBOOK artifact is retired -- a tribute now produces a
    # video (+ PDF for print), rendered by flashback.workers.tribute_render via
    # the tribute_video path above. (The standalone /storybooks keepsake books
    # are a separate feature and are unaffected.)
    raise HTTPException(
        status_code=410,
        detail=(
            "tribute storybook is retired; tributes now produce a video "
            "(+ PDF for print). Call with artifact_kind='tribute_video'."
        ),
    )


# How long a freshly composed render may stay in flight before /generate lets a
# retry through. A tribute video renders in minutes; anything still 'generating'
# well past that is a DEAD render (worker not deployed / crashed hard -- the prod
# failure mode from 2026-07-16), and the contributor has to be able to retry.
# Ordinary render failures unblock immediately: the worker writes
# status='failed', which this guard never blocks.
RENDER_INFLIGHT_GRACE = timedelta(minutes=30)


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse a stored ISO timestamp; None when absent or unparseable."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _reject_duplicate_render(*, tribute_id: UUID, tribute: dict) -> None:
    """409 a repeat /generate so one more click can't buy one more render.

    /generate is the FIRST mint and is not retry-safe: it overwrites the render
    context with fresh presigned URLs + a new ``composed_at``, flips the row
    back to 'generating', and enqueues another (paid) render -- which also
    stales the in-flight one and leaves the card holding a finished video_url
    while the status says 'generating'. Node's FE is supposed to gate the button
    (TributesController.generate: "NOT retry-safe"), but the boundary that
    spends the money owns the guard. /regenerate and /edit remain the deliberate
    re-render paths.
    """
    row_status = (tribute.get("status") or "").strip()
    if row_status in ("complete", "superseded") or tribute.get("video_url"):
        raise HTTPException(
            status_code=409,
            detail=(
                "tribute already rendered; use /regenerate to re-roll it "
                "(or /edit to change it)"
            ),
        )
    if row_status != "generating":
        return
    composed_at = _parse_iso(tribute.get("render_composed_at"))
    if composed_at is None:
        # An in-flight render we cannot age. Let the retry through rather than
        # wedging the tribute shut forever.
        log.warning(
            "tribute.inflight_render_undatable", tribute_id=str(tribute_id)
        )
        return
    age = datetime.now(timezone.utc) - composed_at
    if age < RENDER_INFLIGHT_GRACE:
        raise HTTPException(
            status_code=409,
            detail=(
                "a tribute render is already in progress; wait for it to "
                "finish before generating again"
            ),
        )
    log.info(
        "tribute.stale_render_retry_allowed",
        tribute_id=str(tribute_id),
        age_seconds=int(age.total_seconds()),
    )


async def _generate_video(
    *,
    tribute_id: UUID,
    body: TributeGenerateRequest,
    db_pool: AsyncConnectionPool,
    tribute: dict,
    progress,
    ground_truth: dict | None,
    tribute_render_queue: "TributeRenderQueueProducer | None",
    settings: "HttpConfig | None" = None,
) -> TributeGenerateResponse:
    """Python-owned tribute video: store the render context (assembly inputs +
    presigned URLs) on the row and enqueue tribute_render. Unlocks at 100%. The
    worker assembles the Book, renders MP4 + PDF, and Node writes the URLs on
    completion -- assembly is NOT done here so the request returns fast.

    Gate is `ready` (the hard gate), NOT percent==100 (two-meter model, design
    2026-07-22): soft slots (appearance/signature, unless a campaign requires
    them) add to the bar but never block generation, so a video unlocks before
    the bar fills."""
    _reject_duplicate_render(tribute_id=tribute_id, tribute=tribute)
    if not progress.ready:
        raise HTTPException(
            status_code=409,
            detail=(
                "tribute not ready to generate "
                f"(percent={progress.percent}); need "
                + ("enough shared stories"
                   if progress.kind == "standalone"
                   else "enough stories and your message")
            ),
        )
    if not body.video_put_url or not body.pdf_put_url:
        raise HTTPException(
            status_code=400,
            detail="video_put_url and pdf_put_url are required for tribute_video",
        )

    # Tribute story: theme-tagged moments, falling back to the qualifying
    # pool. Config (campaign + relationship profile + visual theme) is
    # resolved HERE and snapshotted below — the render worker reads only the
    # snapshot (spec 2026-07-14 §6.4).
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            themed: list = []
            if tribute.get("theme_id"):
                themed = await fetch_theme_scene_moments_async(
                    cur, person_id=body.person_id,
                    theme_id=tribute["theme_id"], limit=STORYBOOK_MAX_PAGES)
            candidates = themed
            # Widen when the theme pool is THIN, not only when it is empty --
            # the ready gate counts person-wide, so a thin theme pool means the
            # book is built from less than the gate promised.
            if len(themed) < MEMORIES_TARGET:
                candidates = choose_candidate_pool(
                    themed,
                    await fetch_scene_moments_async(
                        cur, person_id=body.person_id,
                        limit=STORYBOOK_MAX_PAGES),
                    target=MEMORIES_TARGET)
            # Both fetches return newest-extracted first; the book needs
            # telling order (see order_candidates_for_narrative).
            candidates = order_candidates_for_narrative(candidates)
            archetype_answers = await fetch_render_archetype_answers_async(
                cur, tribute_id=tribute_id)
            campaign, profile, directives, visual_theme = (
                await _resolve_render_config(
                    cur, person_id=str(body.person_id),
                    tribute_id=tribute_id, campaign_slug=body.campaign,
                    settings=settings))
            # No backstop stamp. It was added for prod 2026-07-16 (campaign rows
            # arriving unstamped because the entry path didn't carry the slug),
            # but a row's campaign identity is now settled at creation: the entry
            # path stamps at insert, and the open-tribute lookup no longer adopts
            # a standalone row into a campaign flow. So the only row this could
            # still change is a STANDALONE keepsake -- and since 0048 campaign_id
            # decides meter_kind, converting one retroactively adds a message slot
            # it was never asked to fill. Prod 2026-07-28: three keepsakes
            # rendered fine, then read 65% + not-ready with a finished video on
            # them (Srinidhi, Bot, Padma). A keepsake stays a keepsake; the render
            # still gets the campaign skin from the snapshot.

    gt_scene = render_ground_truth_block(ground_truth, "scene_subject") or ""

    composed_at = datetime.now(timezone.utc).isoformat()
    deage_default = directives.deage_cover if directives is not None else False
    # Store the assembly INPUTS, not a pre-built Book -- the worker assembles
    # the storybook (a ~30s big-LLM call) at render time so this request stays
    # fast and never trips Node's HTTP timeout.
    context = build_context_dict(
        subject_name=tribute["person_name"] or "",
        relationship=tribute["person_relationship"],
        gt_context=gt_scene,
        candidates=candidates,
        message_text=tribute["message_text"] or "",
        archetype_leads=leads_to_lines(build_leads(archetype_answers)),
        # One page per memory, capped. A fixed 13 made the assembler stretch a
        # thin pool into filler -- a 3-memory tribute came back as twelve
        # interchangeable affirmations ("Every bad day ended better once he
        # arrived") with no place, object or event in them.
        n_pages=max(1, min(len(candidates), STORYBOOK_MAX_PAGES)),
        video_put_url=body.video_put_url,
        pdf_put_url=body.pdf_put_url,
        poster_put_url=body.poster_put_url or "",
        gender=tribute.get("gender"),
        contributor_gender=tribute.get("contributor_gender"),
        prime_photo_get_url=body.prime_photo_get_url or "",
        deage=deage_default and not body.cover_photo_is_prime_years,
        composed_at=composed_at,
        style=_style_dict(visual_theme),
        profile_id=profile.id if profile is not None else "",
        campaign_id=campaign.id,
        voice_block=directives.voice_block if directives else "",
        opener_style=directives.opener_style if directives else "",
        art_mood=directives.art_mood if directives else "",
        narrative_block=directives.narrative_block if directives else "",
        fallback_opener=directives.fallback_opener if directives else "",
        fallback_closing=directives.fallback_closing if directives else "",
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await write_tribute_generation_context_async(
                    cur, tribute_id=tribute_id,
                    artifact_kind="tribute_video", context=context)
                await set_status_async(
                    cur, tribute_id=tribute_id, status="generating")

    job_id = str(uuid4())
    enqueued = False
    if tribute_render_queue is not None:
        try:
            msg_id = await tribute_render_queue.push(
                job_id=job_id, tribute_id=str(tribute_id),
                person_id=str(body.person_id), composed_at=composed_at)
            enqueued = msg_id is not None
        except Exception:
            log.warning("tribute.render_enqueue_failed",
                        tribute_id=str(tribute_id), exc_info=True)

    return TributeGenerateResponse(
        job_id=job_id, tribute_id=tribute_id, artifact_kind="tribute_video",
        enqueued=enqueued, percent=progress.percent, ready=progress.ready,
        scene_count=min(len(candidates), STORYBOOK_MAX_PAGES))


async def _rederive_candidates(
    *, tribute_id: UUID, person_id: UUID, db_pool: AsyncConnectionPool
) -> list[dict]:
    """Compose the candidate pool the way /generate does, from live moments.

    Same two-step as ``_generate_video``: the tribute theme's tagged moments,
    widened to the person-wide qualifying pool when the theme pool is thinner
    than the story floor.
    """
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            row = await fetch_tribute_for_assembly_async(cur, tribute_id=tribute_id)
            themed: list = []
            if row and row.get("theme_id"):
                themed = await fetch_theme_scene_moments_async(
                    cur, person_id=person_id, theme_id=row["theme_id"],
                    limit=STORYBOOK_MAX_PAGES)
            candidates = themed
            if len(themed) < MEMORIES_TARGET:
                candidates = choose_candidate_pool(
                    themed,
                    await fetch_scene_moments_async(
                        cur, person_id=person_id, limit=STORYBOOK_MAX_PAGES),
                    target=MEMORIES_TARGET)
    return order_candidates_for_narrative(candidates)


async def _reenqueue_tribute_render(
    *,
    tribute_id: UUID,
    person_id: UUID,
    stored: dict,
    video_put_url: str,
    pdf_put_url: str,
    poster_put_url: str | None,
    prime_photo_get_url: str | None,
    edit_instructions: list[str],
    db_pool: AsyncConnectionPool,
    tribute_render_queue: "TributeRenderQueueProducer | None",
) -> TributeGenerateResponse:
    """Re-render a tribute_video from its stored inputs (shared by regenerate +
    edit). Rebuilds the context through the canonical builder so the shape is
    guaranteed and stray keys drop. Content inputs (candidates, message, leads,
    deage) are reused verbatim; the CONFIG layer (voice directives + visual
    style) is re-resolved fresh — manual regenerate is the recovery path after
    a CRM edit (spec 2026-07-14 §6.4). The bumped ``composed_at`` makes any
    in-flight older render go stale and skip."""
    composed_at = datetime.now(timezone.utc).isoformat()

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            campaign, profile, directives, visual_theme = (
                await _resolve_render_config(
                    cur, person_id=str(person_id), tribute_id=tribute_id,
                    campaign_id_hint=stored.get("campaign_id") or None))
            # Leads come off the row rather than the snapshot: regenerate is
            # the recovery path, and snapshots taken before leads were wired
            # into the render all carry an empty list.
            archetype_answers = await fetch_render_archetype_answers_async(
                cur, tribute_id=tribute_id)
            # No backstop stamp here. It used to mirror /generate's, but the
            # only row it could ever change is one with campaign_id NULL --
            # which since 0048 IS a standalone keepsake, and converting one
            # retroactively adds a message slot it never had (the 65%-with-a-
            # finished-video rows, prod 2026-07-28). The snapshot still pins
            # the campaign for the render itself.
    if directives is not None:
        style = _style_dict(visual_theme)
        profile_id = profile.id if profile is not None else ""
        campaign_id = campaign.id
        voice_block = directives.voice_block
        opener_style = directives.opener_style
        art_mood = directives.art_mood
        narrative_block = directives.narrative_block
        fallback_opener = directives.fallback_opener
        fallback_closing = directives.fallback_closing
    else:
        # Config unreachable — carry the prior snapshot's fields unchanged.
        style = stored.get("style") or None
        profile_id = stored.get("profile_id") or ""
        campaign_id = stored.get("campaign_id") or ""
        voice_block = stored.get("voice_block") or ""
        opener_style = stored.get("opener_style") or ""
        art_mood = stored.get("art_mood") or ""
        narrative_block = stored.get("narrative_block") or ""
        fallback_opener = stored.get("fallback_opener") or ""
        fallback_closing = stored.get("fallback_closing") or ""

    # Candidates are reused verbatim, ORDER INCLUDED: /generate already put
    # them in telling order, and re-ordering here would reverse it back. The
    # page count is re-derived (idempotent) so a regenerate repairs a snapshot
    # taken while it was still a fixed 13.
    candidates = list(stored.get("candidates") or [])
    # ...but a slice thinner than the story floor is not a choice, it is damage:
    # snapshots composed before 3fb262f carry only the theme-tagged moments (one
    # memory for a legacy with eighteen), and reusing them verbatim meant the
    # 1-page book could never heal -- /generate re-derives, but it 409s once a
    # video exists. Re-read the live pool instead, exactly as /generate composes
    # it (fresh fetch => narrative order applied here, not reversed).
    if len(candidates) < MEMORIES_TARGET:
        widened = await _rederive_candidates(
            tribute_id=tribute_id, person_id=person_id, db_pool=db_pool)
        if len(widened) > len(candidates):
            log.info("tribute.rerender_widened_thin_slice",
                     tribute_id=str(tribute_id),
                     stored=len(candidates), widened=len(widened))
            candidates = widened
    leads = leads_to_lines(build_leads(archetype_answers))

    context = build_context_dict(
        subject_name=stored.get("subject_name") or "",
        relationship=stored.get("relationship"),
        gt_context=stored.get("gt_context") or "",
        gender=stored.get("gender"),
        contributor_gender=stored.get("contributor_gender"),
        candidates=candidates,
        message_text=stored.get("message_text") or "",
        archetype_leads=leads or list(stored.get("archetype_leads") or []),
        edit_instructions=edit_instructions,
        n_pages=max(1, min(len(candidates), STORYBOOK_MAX_PAGES)),
        blend=stored.get("blend") or "cream",
        transition=stored.get("transition") or "bleed",
        fps=int(stored.get("fps") or 30),
        deage=bool(stored.get("deage") or False),
        video_put_url=video_put_url,
        pdf_put_url=pdf_put_url,
        poster_put_url=poster_put_url or "",
        prime_photo_get_url=prime_photo_get_url or "",
        composed_at=composed_at,
        style=style,
        profile_id=profile_id,
        campaign_id=campaign_id,
        voice_block=voice_block,
        opener_style=opener_style,
        art_mood=art_mood,
        narrative_block=narrative_block,
        fallback_opener=fallback_opener,
        fallback_closing=fallback_closing,
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await write_tribute_generation_context_async(
                    cur, tribute_id=tribute_id,
                    artifact_kind=CONTEXT_KEY, context=context)
                await set_status_async(
                    cur, tribute_id=tribute_id, status="generating")

    job_id = str(uuid4())
    enqueued = False
    if tribute_render_queue is not None:
        try:
            msg_id = await tribute_render_queue.push(
                job_id=job_id, tribute_id=str(tribute_id),
                person_id=str(person_id), composed_at=composed_at)
            enqueued = msg_id is not None
        except Exception:
            log.warning("tribute.rerender_enqueue_failed",
                        tribute_id=str(tribute_id), exc_info=True)

    scene_count = min(len(context["candidates"]), STORYBOOK_MAX_PAGES)
    return TributeGenerateResponse(
        job_id=job_id, tribute_id=tribute_id, artifact_kind="tribute_video",
        enqueued=enqueued, percent=100, ready=True, scene_count=scene_count)


@router.post(
    "/tributes/{tribute_id}/regenerate", response_model=TributeGenerateResponse
)
async def regenerate_tribute(
    tribute_id: UUID,
    body: TributeRegenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    tribute_render_queue: "TributeRenderQueueProducer | None" = Depends(
        get_tribute_render_queue
    ),
) -> TributeGenerateResponse:
    """Re-render a tribute video from the SAME stored assembly inputs.

    Reuses the prior tribute_video context (candidates, message, leads, knobs)
    verbatim and only overlays fresh Node-minted presigned URLs + a new
    composed_at -- the old URLs have expired by now. The bumped composed_at
    makes any in-flight older render go stale and skip. The worker re-assembles
    the Book from the same inputs, so the LLM re-rolls a fresh take.
    """
    if not body.video_put_url or not body.pdf_put_url:
        raise HTTPException(
            status_code=400,
            detail="video_put_url and pdf_put_url are required",
        )

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            fetched = await fetch_tribute_generation_context_async(
                cur, tribute_id=tribute_id, artifact_kind=CONTEXT_KEY)
    if fetched is None or fetched[0] != str(body.person_id):
        raise HTTPException(status_code=404, detail="tribute not found")
    stored = fetched[1]
    if not stored:
        raise HTTPException(
            status_code=404,
            detail=(
                "no prior tribute_video render to regenerate; "
                "call /generate first"
            ),
        )

    # Reuse the stored edit instructions verbatim: regenerate re-rolls the
    # CURRENT (possibly edited) state, it does not revert prior edits.
    return await _reenqueue_tribute_render(
        tribute_id=tribute_id, person_id=body.person_id, stored=stored,
        video_put_url=body.video_put_url, pdf_put_url=body.pdf_put_url,
        poster_put_url=body.poster_put_url,
        prime_photo_get_url=body.prime_photo_get_url,
        edit_instructions=list(stored.get("edit_instructions") or []),
        db_pool=db_pool, tribute_render_queue=tribute_render_queue)


@router.post("/tributes/{tribute_id}/edit", response_model=TributeGenerateResponse)
async def edit_tribute(
    tribute_id: UUID,
    body: TributeEditRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    tribute_render_queue: "TributeRenderQueueProducer | None" = Depends(
        get_tribute_render_queue
    ),
) -> TributeGenerateResponse:
    """Re-render a tribute video with cumulative free-text adjustments.

    Like moments' /edit: Node sends the full prior_instructions list each call;
    the agent applies prior_instructions + [instructions] as the family's edit
    requests, which shape both captions and art directions. Reuses the stored
    inputs otherwise and overlays fresh presigned URLs (the old ones expired).
    """
    if not body.video_put_url or not body.pdf_put_url:
        raise HTTPException(
            status_code=400,
            detail="video_put_url and pdf_put_url are required",
        )
    effective = [
        s.strip()
        for s in [*(body.prior_instructions or []), body.instructions or ""]
        if s and s.strip()
    ]
    if not effective:
        raise HTTPException(
            status_code=400,
            detail=(
                "instructions (or prior_instructions) required; "
                "use /regenerate to re-render unchanged"
            ),
        )

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            fetched = await fetch_tribute_generation_context_async(
                cur, tribute_id=tribute_id, artifact_kind=CONTEXT_KEY)
    if fetched is None or fetched[0] != str(body.person_id):
        raise HTTPException(status_code=404, detail="tribute not found")
    stored = fetched[1]
    if not stored:
        raise HTTPException(
            status_code=404,
            detail=(
                "no prior tribute_video render to edit; call /generate first"
            ),
        )

    return await _reenqueue_tribute_render(
        tribute_id=tribute_id, person_id=body.person_id, stored=stored,
        video_put_url=body.video_put_url, pdf_put_url=body.pdf_put_url,
        poster_put_url=body.poster_put_url,
        prime_photo_get_url=body.prime_photo_get_url,
        edit_instructions=effective,
        db_pool=db_pool, tribute_render_queue=tribute_render_queue)


@router.post(
    "/tributes/{tribute_id}/edit-suggestions",
    response_model=TributeEditSuggestionsResponse,
)
async def tribute_edit_suggestions(
    tribute_id: UUID,
    body: TributeEditSuggestionsRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    settings: "HttpConfig" = Depends(get_http_config),
) -> TributeEditSuggestionsResponse:
    """Contextual edit chips for a rendered tribute (small LLM, best-effort).

    Reads the stored render inputs (memories + message + prior edits) and
    proposes subject-specific nudges the user can tap; a tapped chip's
    `instruction` is what Node sends to /edit. Falls back to a generic catalog
    on LLM failure. 404 if the tribute was never generated.
    """
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            fetched = await fetch_tribute_generation_context_async(
                cur, tribute_id=tribute_id, artifact_kind=CONTEXT_KEY)
    if fetched is None or fetched[0] != str(body.person_id):
        raise HTTPException(status_code=404, detail="tribute not found")
    stored = fetched[1]
    if not stored:
        raise HTTPException(
            status_code=404,
            detail="no prior tribute_video render; call /generate first",
        )

    suggestions = await generate_edit_suggestions(
        settings=settings,
        subject_name=stored.get("subject_name") or "",
        relationship=stored.get("relationship"),
        candidates=list(stored.get("candidates") or []),
        message_text=stored.get("message_text") or "",
        prior_instructions=list(stored.get("edit_instructions") or []),
    )
    return TributeEditSuggestionsResponse(
        suggestions=[TributeEditSuggestion(**s) for s in suggestions])


@router.get("/tribute-campaigns", response_model=TributeCampaignsResponse)
async def get_tribute_campaigns(
    person_id: UUID | None = Query(
        None,
        description=(
            "Scope is_active / active_featured_slug to this legacy's "
            "relationship group (0041 targeting). Without it, targeted "
            "campaigns list with is_active=false surfaces intact."
        ),
    ),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> TributeCampaignsResponse:
    """Public campaign list + which campaign is featured today (for Node).

    DB-backed (tribute CRM): published campaign rows, neutral first — the
    same shape the code registry served before migration 0039. With
    ``person_id``, relationship-targeted campaigns count as active only
    for matching legacies (cached group; no LLM call here).
    """
    today = datetime.now(timezone.utc).date()
    group: str | None = None
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            if person_id is not None:
                await cur.execute(
                    "SELECT relationship_group FROM persons WHERE id = %s",
                    (str(person_id),),
                )
                row = await cur.fetchone()
                group = row[0] if row else None
            active = await active_featured_campaign_db(cur, today)
            rows = await list_rows(cur, "tribute_campaigns")

    def _applies(groups: tuple[str, ...]) -> bool:
        if not groups:
            return True
        # Person-scoped: require a matching known group. Unscoped calls
        # keep the pre-0041 global view.
        if person_id is None:
            return True
        return group is not None and group in groups

    out = [
        TributeCampaignOut(
            slug=NEUTRAL_CAMPAIGN.slug,
            display_name=NEUTRAL_CAMPAIGN.display_name,
            featured=False,
            is_active=False,
        )
    ]
    for r in rows:
        if r.get("state") != "published":
            continue
        start, end = r.get("active_start"), r.get("active_end")
        targeting = tuple(r.get("relationship_groups") or ())
        is_active = bool(
            r.get("featured") and start and end and start <= today <= end
            and _applies(targeting)
        )
        out.append(
            TributeCampaignOut(
                slug=r["slug"],
                display_name=r["display_name"],
                featured=bool(r.get("featured")),
                is_active=is_active,
                active_start=start.isoformat() if start else None,
                active_end=end.isoformat() if end else None,
                relationship_groups=list(targeting),
            )
        )
    active_slug = None
    if active is not None and _applies(active.relationship_groups):
        active_slug = active.slug
    return TributeCampaignsResponse(
        campaigns=out,
        active_featured_slug=active_slug,
    )
