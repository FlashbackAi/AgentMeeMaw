"""Tribute generation endpoint.

POST /tributes/{id}/generate gates on the tribute_status view, assembles a
script, composes the artifact-kind context, writes it (keyed) to the
tribute row, flips status to 'generating', and pushes a trigger-only
artifact_generation job. Node's compiled renderer reads the context.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
from flashback.tribute.config_repository import (
    active_featured_campaign_db,
    fetch_campaign_by_id,
    fetch_profile_by_group,
    fetch_visual_theme_by_id,
    list_rows,
    resolve_campaign_db,
)
from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    CampaignConfig,
    VisualThemeConfig,
)
from flashback.tribute.progress import (
    fetch_tribute_progress_async,
    progress_to_payload,
)
from flashback.tribute.invitation import resolve_invitation_copy
from flashback.tribute.message_capture import polish_and_store_message
from flashback.tribute.relationships import ensure_relationship_group
from flashback.tribute.repository import (
    fetch_scene_moments_async,
    fetch_theme_scene_moments_async,
    fetch_tribute_campaign_id_async,
    fetch_tribute_for_assembly_async,
    fetch_tribute_generation_context_async,
    set_status_async,
    stamp_tribute_campaign_async,
    write_tribute_generation_context_async,
)
from flashback.tribute_video.context import CONTEXT_KEY, build_context_dict
from flashback.tribute_video.edit_suggestions import generate_edit_suggestions
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
    settings=None,
):
    """Resolve (campaign, profile, directives, visual_theme) for a render.

    Campaign: the tribute row's stamped campaign_id wins, else the slug the
    caller passed, else neutral. Profile: the person's cached relationship
    group (resolved lazily when settings are provided), safety-floored on
    'other'. Never raises — a render never blocks on config (spec §6.5).
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
    }


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
                "SELECT person_id::text, status FROM tributes WHERE id = %s",
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
    completion -- assembly is NOT done here so the request returns fast."""
    if progress.percent < 100:
        raise HTTPException(
            status_code=409,
            detail=f"tribute not at 100% (percent={progress.percent})",
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
            candidates: list = []
            if tribute.get("theme_id"):
                candidates = await fetch_theme_scene_moments_async(
                    cur, person_id=body.person_id,
                    theme_id=tribute["theme_id"], limit=STORYBOOK_MAX_PAGES)
            if not candidates:
                candidates = await fetch_scene_moments_async(
                    cur, person_id=body.person_id, limit=STORYBOOK_MAX_PAGES)
            campaign, profile, directives, visual_theme = (
                await _resolve_render_config(
                    cur, person_id=str(body.person_id),
                    tribute_id=tribute_id, campaign_slug=body.campaign,
                    settings=settings))
            # Backstop stamp (prod 2026-07-16: rows arrived unstamped because
            # the entry path didn't carry the campaign slug). The snapshot
            # already pins the campaign; stamping the ROW is what lets the
            # tribute_status gallery label each video by campaign. No-op when
            # already stamped.
            if campaign.id:
                await stamp_tribute_campaign_async(
                    cur, tribute_id=tribute_id, campaign_id=campaign.id)
                await conn.commit()

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
        archetype_leads=[],
        n_pages=STORYBOOK_MAX_PAGES,
        video_put_url=body.video_put_url,
        pdf_put_url=body.pdf_put_url,
        poster_put_url=body.poster_put_url or "",
        prime_photo_get_url=body.prime_photo_get_url or "",
        deage=deage_default and not body.cover_photo_is_prime_years,
        composed_at=composed_at,
        style=_style_dict(visual_theme),
        profile_id=profile.id if profile is not None else "",
        campaign_id=campaign.id,
        voice_block=directives.voice_block if directives else "",
        opener_style=directives.opener_style if directives else "",
        art_mood=directives.art_mood if directives else "",
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
                    cur, person_id=str(person_id), tribute_id=tribute_id))
    if directives is not None:
        style = _style_dict(visual_theme)
        profile_id = profile.id if profile is not None else ""
        campaign_id = campaign.id
        voice_block = directives.voice_block
        opener_style = directives.opener_style
        art_mood = directives.art_mood
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
        fallback_opener = stored.get("fallback_opener") or ""
        fallback_closing = stored.get("fallback_closing") or ""

    context = build_context_dict(
        subject_name=stored.get("subject_name") or "",
        relationship=stored.get("relationship"),
        gt_context=stored.get("gt_context") or "",
        candidates=list(stored.get("candidates") or []),
        message_text=stored.get("message_text") or "",
        archetype_leads=list(stored.get("archetype_leads") or []),
        edit_instructions=edit_instructions,
        n_pages=int(stored.get("n_pages") or STORYBOOK_MAX_PAGES),
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
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> TributeCampaignsResponse:
    """Public campaign list + which campaign is featured today (for Node).

    DB-backed (tribute CRM): published campaign rows, neutral first — the
    same shape the code registry served before migration 0039.
    """
    today = datetime.now(timezone.utc).date()
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            active = await active_featured_campaign_db(cur, today)
            rows = await list_rows(cur, "tribute_campaigns")

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
        is_active = bool(
            r.get("featured") and start and end and start <= today <= end
        )
        out.append(
            TributeCampaignOut(
                slug=r["slug"],
                display_name=r["display_name"],
                featured=bool(r.get("featured")),
                is_active=is_active,
                active_start=start.isoformat() if start else None,
                active_end=end.isoformat() if end else None,
            )
        )
    return TributeCampaignsResponse(
        campaigns=out,
        active_featured_slug=active.slug if active else None,
    )
