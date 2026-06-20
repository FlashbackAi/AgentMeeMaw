"""On-demand storybook endpoints.

A legacy can hold many storybooks, each minted on request (Node/user-triggered)
rather than auto-generated at session wrap. These endpoints mirror the moment
artifact regenerate/edit surface but live on their own router because a
storybook is a multi-scene book, not a single image, and ``storybook`` is not
one of the generic artifact ``record_type``s.

  * POST /storybooks                      -- mint a new (optionally scoped) book
  * POST /storybooks/{id}/regenerate      -- re-render with a new preset / tags
  * POST /storybooks/{id}/edit            -- reshape text + scenes per edits

All three compose the full generation context, write it to the row, and push a
trigger-only ``artifact_generation`` job; Node's renderer reads the context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.artifacts.presets import resolve_preset
from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_artifact_generation_queue,
    get_db_pool,
    get_http_config,
)
from flashback.http.models import (
    StorybookEditRequest,
    StorybookGenerateRequest,
    StorybookJobResponse,
    StorybookRegenerateRequest,
)
from flashback.storybook.generation import (
    StorybookGenerationResult,
    StorybookNotFound,
    StorybookTooThin,
    edit_storybook,
    generate_storybook,
    regenerate_storybook,
)

if TYPE_CHECKING:
    from flashback.queues.artifact_generation import (
        ArtifactGenerationQueueProducer,
    )

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.storybooks")


def _resolve_preset_or_400(preset: str | None) -> None:
    try:
        resolve_preset(preset)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def _to_response(
    result: StorybookGenerationResult,
    *,
    person_id: UUID,
    source: str,
) -> StorybookJobResponse:
    return StorybookJobResponse(
        job_id=result.job_id,
        storybook_id=UUID(result.storybook_id),
        person_id=person_id,
        status="generating",
        source=source,  # type: ignore[arg-type]
        tags=result.tags,
        moments_count=result.moments_count,
        scene_count=result.scene_count,
        enqueued=result.enqueued,
    )


@router.post("/storybooks", response_model=StorybookJobResponse)
async def create_storybook(
    body: StorybookGenerateRequest,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> StorybookJobResponse:
    _resolve_preset_or_400(body.preset)
    scope = body.scope
    try:
        result = await generate_storybook(
            db_pool=db_pool,
            settings=cfg,
            artifact_queue=artifact_queue,
            person_id=str(body.person_id),
            theme_id=str(scope.theme_id) if scope and scope.theme_id else None,
            life_period=(scope.life_period if scope else None) or None,
            preset=body.preset,
        )
    except StorybookTooThin as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _to_response(result, person_id=body.person_id, source="manual")


@router.post("/storybooks/{storybook_id}/regenerate", response_model=StorybookJobResponse)
async def regenerate_storybook_route(
    storybook_id: UUID,
    body: StorybookRegenerateRequest,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> StorybookJobResponse:
    _resolve_preset_or_400(body.preset)
    try:
        result = await regenerate_storybook(
            db_pool=db_pool,
            settings=cfg,
            artifact_queue=artifact_queue,
            storybook_id=str(storybook_id),
            person_id=str(body.person_id),
            preset=body.preset,
            tags=body.tags,
        )
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _to_response(result, person_id=body.person_id, source="regenerate")


@router.post("/storybooks/{storybook_id}/edit", response_model=StorybookJobResponse)
async def edit_storybook_route(
    storybook_id: UUID,
    body: StorybookEditRequest,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> StorybookJobResponse:
    _resolve_preset_or_400(body.preset)
    try:
        result = await edit_storybook(
            db_pool=db_pool,
            settings=cfg,
            artifact_queue=artifact_queue,
            storybook_id=str(storybook_id),
            person_id=str(body.person_id),
            instructions=body.instructions,
            prior_instructions=body.prior_instructions,
            preset=body.preset,
            tags=body.tags,
        )
    except StorybookTooThin as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _to_response(result, person_id=body.person_id, source="edit")
