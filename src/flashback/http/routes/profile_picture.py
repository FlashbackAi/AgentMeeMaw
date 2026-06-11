"""Profile-picture generation endpoints.

``POST /persons/{person_id}/profile-picture``
    Enqueue a regeneration job (no-reference or with-reference).

``POST /persons/{person_id}/profile-picture/edit``
    Re-compose the prompt with user instructions and re-enqueue.

Auth: ``require_service_token``, same as every other write route.

Postgres-authoritative artifact-generation model: the route composes the
full portrait context and writes it to
``persons.latest_generation_context`` BEFORE pushing the (trigger-only)
SQS message. Node's worker reads the context from Postgres at job time.
See CLAUDE.md §3 and migration 0023.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.artifacts import (
    build_generation_context,
    write_latest_generation_context_async,
)
from flashback.artifacts.presets import resolve_preset
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_profile_picture_queue
from flashback.http.models import (
    ProfilePictureEditRequest,
    ProfilePictureGenerateRequest,
    ProfilePictureJobResponse,
)
from flashback.persons import get_person_by_id
from flashback.profile_picture import NEGATIVE_PROMPT, compose_image_prompt

if TYPE_CHECKING:
    from flashback.queues.profile_picture import ProfilePictureQueueProducer

router = APIRouter(
    prefix="/persons",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.profile_picture")


@router.post(
    "/{person_id}/profile-picture",
    response_model=ProfilePictureJobResponse,
)
async def regenerate(
    person_id: UUID,
    body: ProfilePictureGenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    profile_picture_queue: "ProfilePictureQueueProducer | None" = Depends(
        get_profile_picture_queue
    ),
) -> ProfilePictureJobResponse:
    """Enqueue a fresh profile-picture generation job for an existing person."""
    return await _enqueue_portrait_job(
        person_id=person_id,
        instructions=body.instructions,
        prior_instructions=[],
        preset_input=body.preset,
        reference_s3_key=body.reference_s3_key,
        source="regenerate",
        db_pool=db_pool,
        profile_picture_queue=profile_picture_queue,
    )


@router.post(
    "/{person_id}/profile-picture/edit",
    response_model=ProfilePictureJobResponse,
)
async def edit(
    person_id: UUID,
    body: ProfilePictureEditRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    profile_picture_queue: "ProfilePictureQueueProducer | None" = Depends(
        get_profile_picture_queue
    ),
) -> ProfilePictureJobResponse:
    """Re-compose the prompt with user instructions and enqueue a new job."""
    return await _enqueue_portrait_job(
        person_id=person_id,
        instructions=body.instructions,
        prior_instructions=body.prior_instructions,
        preset_input=body.preset,
        reference_s3_key=body.reference_s3_key,
        source="edit",
        db_pool=db_pool,
        profile_picture_queue=profile_picture_queue,
    )


async def _enqueue_portrait_job(
    *,
    person_id: UUID,
    instructions: str | None,
    prior_instructions: list[str],
    preset_input: str | None,
    reference_s3_key: str | None,
    source: str,
    db_pool: AsyncConnectionPool,
    profile_picture_queue: "ProfilePictureQueueProducer | None",
) -> ProfilePictureJobResponse:
    person = await get_person_by_id(db_pool, person_id=person_id)
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="person not found"
        )

    try:
        preset_slug = resolve_preset(preset_input)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    mode = "with_reference" if reference_s3_key else "no_reference"
    job_id = str(uuid4())
    stacked_instructions: list[str] = (
        [*prior_instructions, instructions] if instructions else list(prior_instructions)
    )
    ground_truth = await fetch_ground_truth(db_pool, person_id)
    image_prompt = compose_image_prompt(
        name=person.name,
        gender=person.gender,
        relationship=person.relationship,
        user_instructions=stacked_instructions or None,
        preset=preset_slug,
        ground_truth_context=render_ground_truth_block(ground_truth, "portrait")
        or None,
    )
    context = build_generation_context(
        prompt=image_prompt,
        negative_prompt=NEGATIVE_PROMPT,
        mode=mode,
        reference_s3_key=reference_s3_key,
        preset=preset_slug,
        source=source,
    )

    # Write context to Postgres FIRST. Node's worker reads the prompt
    # from this column; the SQS message is just a trigger.
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await write_latest_generation_context_async(
                cur,
                table="persons",
                record_id=person_id,
                context=context,
            )

    enqueued = False
    if profile_picture_queue is not None:
        try:
            msg_id = await profile_picture_queue.push(
                job_id=job_id,
                person_id=person_id,
                source=source,
                composed_at=context["composed_at"],
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning(
                "profile_picture.enqueue_failed",
                person_id=str(person_id),
                source=source,
                exc_info=True,
            )

    return ProfilePictureJobResponse(
        job_id=job_id,
        person_id=person_id,
        mode=mode,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        preset=preset_slug,
        enqueued=enqueued,
    )
