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
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
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
    TributeGenerateRequest,
    TributeGenerateResponse,
)
from flashback.tribute.campaigns import (
    active_featured_campaign,
    list_campaigns,
    resolve_campaign,
)
from flashback.tribute.progress import fetch_tribute_progress_async
from flashback.tribute.repository import (
    fetch_scene_moments_async,
    fetch_theme_scene_moments_async,
    fetch_tribute_for_assembly_async,
    set_status_async,
    write_tribute_generation_context_async,
)
from flashback.tribute_video.assembler import assemble_storybook_video
from flashback.tribute_video.context import build_context_dict
from flashback.tribute.theme import STORYBOOK_MAX_PAGES

if TYPE_CHECKING:
    from flashback.queues.artifact_generation import (
        ArtifactGenerationQueueProducer,
    )
    from flashback.queues.tribute_render import TributeRenderQueueProducer

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.tributes")

@router.post("/tributes/{tribute_id}/generate", response_model=TributeGenerateResponse)
async def generate_tribute(
    tribute_id: UUID,
    body: TributeGenerateRequest,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
    tribute_render_queue: "TributeRenderQueueProducer | None" = Depends(
        get_tribute_render_queue
    ),
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
            cfg=cfg,
            db_pool=db_pool,
            tribute=tribute,
            progress=progress,
            ground_truth=ground_truth,
            tribute_render_queue=tribute_render_queue,
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
    cfg: HttpConfig,
    db_pool: AsyncConnectionPool,
    tribute: dict,
    progress,
    ground_truth: dict | None,
    tribute_render_queue: "TributeRenderQueueProducer | None",
) -> TributeGenerateResponse:
    """Python-owned tribute video: assemble the FD-flow Book, store the render
    context + presigned URLs on the row, enqueue tribute_render. Unlocks at 100%;
    the worker renders MP4 + PDF and Node writes the URLs on completion."""
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

    # FD-flow story: theme-tagged moments, falling back to the qualifying pool.
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

    gt_scene = render_ground_truth_block(ground_truth, "scene_subject") or ""
    book = await assemble_storybook_video(
        settings=cfg,
        subject_name=tribute["person_name"] or "",
        relationship=tribute["person_relationship"],
        gt_context=gt_scene,
        candidates=candidates,
        message_text=tribute["message_text"] or "",
        archetype_leads=[],
        n_pages=STORYBOOK_MAX_PAGES,
    )

    composed_at = datetime.now(timezone.utc).isoformat()
    campaign = resolve_campaign(body.campaign)
    context = build_context_dict(
        subject_name=tribute["person_name"] or "",
        relationship=tribute["person_relationship"],
        gt_context=gt_scene,
        book=book,
        video_put_url=body.video_put_url,
        pdf_put_url=body.pdf_put_url,
        prime_photo_get_url=body.prime_photo_get_url or "",
        deage=campaign.deage_cover and not body.cover_photo_is_prime_years,
        composed_at=composed_at,
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
        scene_count=len(book.beats))


@router.get("/tribute-campaigns", response_model=TributeCampaignsResponse)
async def get_tribute_campaigns() -> TributeCampaignsResponse:
    """Public campaign list + which campaign is featured today (for Node)."""
    today = datetime.now(timezone.utc).date()
    active = active_featured_campaign(today)
    out = []
    for c in list_campaigns():
        is_active = bool(
            c.featured
            and c.active_start
            and c.active_end
            and c.active_start <= today <= c.active_end
        )
        out.append(
            TributeCampaignOut(
                slug=c.slug,
                display_name=c.display_name,
                featured=c.featured,
                is_active=is_active,
                active_start=c.active_start.isoformat() if c.active_start else None,
                active_end=c.active_end.isoformat() if c.active_end else None,
            )
        )
    return TributeCampaignsResponse(
        campaigns=out,
        active_featured_slug=active.slug if active else None,
    )
