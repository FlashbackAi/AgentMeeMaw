"""Collection storybook endpoints (Python render pipeline, spec 2026-06-29).

The user picks one of six fixed collections; the route validates, stores the
render context on the row, and enqueues ``storybook_render``. All heavy LLM
work (curation, script assembly, Gemini art) happens in the worker; Node
mints the presigned URLs up front and LISTENs ``storybook_render_complete``
to write ``pdf_url`` / ``page_urls`` / the cover URLs.

  * GET  /storybook-collections           -- the fixed chooser registry
  * POST /storybooks                      -- mint a new collection book
  * POST /storybooks/{id}/regenerate      -- redraw art, keep the script
  * POST /storybooks/{id}/edit            -- re-assemble with edit requests

The old ``artifact_generation`` path for storybooks is retired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_storybook_render_queue
from flashback.http.models import (
    StorybookCollectionInfo,
    StorybookEditRequest,
    StorybookGenerateRequest,
    StorybookJobResponse,
    StorybookRegenerateRequest,
)
from flashback.storybook.collections import public_collections
from flashback.storybook.generation import (
    BadPageUrls,
    StorybookGenerationResult,
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
    edit_storybook,
    generate_storybook,
    regenerate_storybook,
)

if TYPE_CHECKING:
    from flashback.queues.storybook_render import StorybookRenderQueueProducer

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.storybooks")


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
        collection=result.collection,
        status="generating",
        source=source,  # type: ignore[arg-type]
        moments_count=result.moments_count,
        enqueued=result.enqueued,
    )


@router.get(
    "/storybook-collections", response_model=list[StorybookCollectionInfo]
)
async def list_storybook_collections() -> list[StorybookCollectionInfo]:
    """The fixed collection registry (chooser + presigned-URL mint counts)."""
    return [StorybookCollectionInfo(**c) for c in public_collections()]


@router.post("/storybooks", response_model=StorybookJobResponse)
async def create_storybook(
    body: StorybookGenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    queue: "StorybookRenderQueueProducer | None" = Depends(
        get_storybook_render_queue
    ),
) -> StorybookJobResponse:
    try:
        result = await generate_storybook(
            db_pool=db_pool,
            queue=queue,
            person_id=str(body.person_id),
            collection=body.collection,
            pdf_put_url=body.pdf_put_url,
            cover_put_url=body.cover_put_url,
            page_put_urls=body.page_put_urls,
            anchor_photo_get_url=body.anchor_photo_get_url,
        )
    except (UnknownCollection, BadPageUrls) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except StorybookTooThin as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _to_response(result, person_id=body.person_id, source="manual")


@router.post(
    "/storybooks/{storybook_id}/regenerate",
    response_model=StorybookJobResponse,
)
async def regenerate_storybook_route(
    storybook_id: UUID,
    body: StorybookRegenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    queue: "StorybookRenderQueueProducer | None" = Depends(
        get_storybook_render_queue
    ),
) -> StorybookJobResponse:
    try:
        result = await regenerate_storybook(
            db_pool=db_pool,
            queue=queue,
            storybook_id=str(storybook_id),
            person_id=str(body.person_id),
            pdf_put_url=body.pdf_put_url,
            cover_put_url=body.cover_put_url,
            page_put_urls=body.page_put_urls,
            anchor_photo_get_url=body.anchor_photo_get_url,
        )
    except (UnknownCollection, BadPageUrls) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except StorybookTooThin as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _to_response(result, person_id=body.person_id, source="regenerate")


@router.post(
    "/storybooks/{storybook_id}/edit", response_model=StorybookJobResponse
)
async def edit_storybook_route(
    storybook_id: UUID,
    body: StorybookEditRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    queue: "StorybookRenderQueueProducer | None" = Depends(
        get_storybook_render_queue
    ),
) -> StorybookJobResponse:
    try:
        result = await edit_storybook(
            db_pool=db_pool,
            queue=queue,
            storybook_id=str(storybook_id),
            person_id=str(body.person_id),
            instructions=body.instructions,
            prior_instructions=body.prior_instructions,
            pdf_put_url=body.pdf_put_url,
            cover_put_url=body.cover_put_url,
            page_put_urls=body.page_put_urls,
            anchor_photo_get_url=body.anchor_photo_get_url,
        )
    except (UnknownCollection, BadPageUrls) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except StorybookTooThin as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _to_response(result, person_id=body.person_id, source="edit")
