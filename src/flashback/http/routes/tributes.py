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

from flashback.artifacts.presets import resolve_preset
from flashback.config import HttpConfig
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_artifact_generation_queue,
    get_db_pool,
    get_http_config,
)
from flashback.http.models import (
    TributeCampaignOut,
    TributeCampaignsResponse,
    TributeGenerateRequest,
    TributeGenerateResponse,
)
from flashback.tribute.artifact_context import (
    build_storybook_context,
    build_tribute_video_context,
)
from flashback.tribute.assembly import assemble_tribute_script
from flashback.tribute.campaigns import (
    active_featured_campaign,
    list_campaigns,
    resolve_campaign,
)
from flashback.tribute.progress import fetch_tribute_progress_async
from flashback.tribute.repository import (
    fetch_scene_moments_async,
    fetch_tribute_for_assembly_async,
    set_script_async,
    set_status_async,
    write_tribute_generation_context_async,
)
from flashback.tribute.theme import (
    STORYBOOK_MAX_PAGES,
    STORYBOOK_MIN_PAGES,
)

if TYPE_CHECKING:
    from flashback.queues.artifact_generation import (
        ArtifactGenerationQueueProducer,
    )

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.tributes")

_MAX_VIDEO_SCENES = 6


@router.post("/tributes/{tribute_id}/generate", response_model=TributeGenerateResponse)
async def generate_tribute(
    tribute_id: UUID,
    body: TributeGenerateRequest,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> TributeGenerateResponse:
    try:
        preset_slug = resolve_preset(body.preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 1) Gate + ownership via the status view + the tribute row.
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            tribute = await fetch_tribute_for_assembly_async(cur, tribute_id=tribute_id)
            if tribute is None or tribute["person_id"] != str(body.person_id):
                raise HTTPException(status_code=404, detail="tribute not found")
            progress = await fetch_tribute_progress_async(cur, tribute_id=tribute_id)
            candidates = await fetch_scene_moments_async(
                cur, person_id=body.person_id, limit=12
            )
    ground_truth = await fetch_ground_truth(db_pool, body.person_id)

    if progress is None:
        raise HTTPException(status_code=404, detail="tribute status unavailable")

    if body.artifact_kind == "tribute_video" and not progress.ready:
        raise HTTPException(
            status_code=409,
            detail=f"tribute not ready for video (percent={progress.percent})",
        )
    if body.artifact_kind == "storybook" and len(candidates) < STORYBOOK_MIN_PAGES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"need at least {STORYBOOK_MIN_PAGES} qualifying moments for a "
                f"storybook (have {len(candidates)})"
            ),
        )

    # 2) Assemble the shared script.
    max_scenes = (
        _MAX_VIDEO_SCENES
        if body.artifact_kind == "tribute_video"
        else STORYBOOK_MAX_PAGES - 1
    )
    script = await assemble_tribute_script(
        settings=cfg,
        candidates=candidates,
        message_text=tribute["message_text"] or "",
        person_name=tribute["person_name"] or "",
        person_relationship=tribute["person_relationship"],
        max_scenes=max_scenes,
    )
    moments_by_id = {c["id"]: c for c in candidates}
    gt_scene = render_ground_truth_block(ground_truth, "scene") or None

    # 3) Build the artifact-kind context.
    if body.artifact_kind == "tribute_video":
        campaign = resolve_campaign(body.campaign)
        context = build_tribute_video_context(
            script=script,
            moments_by_id=moments_by_id,
            preset=preset_slug,
            target_duration_seconds=campaign.video_target_seconds,
            ground_truth_context=gt_scene,
        )
    else:
        context = build_storybook_context(
            script=script,
            moments_by_id=moments_by_id,
            preset=preset_slug,
            max_pages=STORYBOOK_MAX_PAGES,
            ground_truth_context=gt_scene,
        )

    # 4) Persist script + keyed context + flip status, all before pushing.
    scene_ids = [s.moment_id for s in script.scenes]
    checklist_state = {s.key: s.filled for s in progress.slots}
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await set_script_async(
                    cur,
                    tribute_id=tribute_id,
                    script={
                        "scenes": [
                            {"moment_id": s.moment_id, "caption": s.caption}
                            for s in script.scenes
                        ],
                        "opening_caption": script.opening_caption,
                        "closing_caption": script.closing_caption,
                        "message_text": script.message_text,
                    },
                    scene_moment_ids=scene_ids,
                    checklist_state=checklist_state,
                )
                await write_tribute_generation_context_async(
                    cur,
                    tribute_id=tribute_id,
                    artifact_kind=body.artifact_kind,
                    context=context,
                )
                await set_status_async(
                    cur, tribute_id=tribute_id, status="generating"
                )

    # 5) Push the trigger-only job.
    job_id = str(uuid4())
    enqueued = False
    if artifact_queue is not None:
        try:
            msg_id = await artifact_queue.push(
                job_id=job_id,
                record_type="tribute",
                record_id=str(tribute_id),
                person_id=str(body.person_id),
                artifact_kind=body.artifact_kind,
                source="auto",
                composed_at=context["composed_at"],
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning(
                "tribute.enqueue_failed", tribute_id=str(tribute_id), exc_info=True
            )

    return TributeGenerateResponse(
        job_id=job_id,
        tribute_id=tribute_id,
        artifact_kind=body.artifact_kind,
        enqueued=enqueued,
        percent=progress.percent,
        ready=progress.ready,
        scene_count=len(script.scenes),
    )


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
