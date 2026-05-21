"""Profile-picture generation endpoints.

``POST /persons/{person_id}/profile-picture``
    Enqueue a regeneration job (no-reference or with-reference).

``POST /persons/{person_id}/profile-picture/edit``
    Re-compose the prompt with user instructions and re-enqueue.

Auth: ``require_service_token``, same as every other write route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_profile_picture_queue
from flashback.http.models import (
    ProfilePictureEditRequest,
    ProfilePictureGenerateRequest,
    ProfilePictureJobResponse,
)
from flashback.persons import get_person_by_id
from flashback.profile_picture import compose_image_prompt, map_gender

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
    person = await get_person_by_id(db_pool, person_id=person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")

    mode = "with_reference" if body.reference_s3_key else "no_reference"
    job_id = str(uuid4())
    gender_contract = map_gender(person.gender)
    image_prompt = compose_image_prompt(
        name=person.name,
        gender=person.gender,
        relationship=person.relationship,
    )

    enqueued = False
    if profile_picture_queue is not None:
        try:
            msg_id = await profile_picture_queue.push(
                job_id=job_id,
                person_id=person_id,
                mode=mode,
                image_prompt=image_prompt,
                source="regenerate",
                name=person.name,
                gender=gender_contract,
                relationship=person.relationship,
                reference_s3_key=body.reference_s3_key,
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning(
                "profile_picture.regenerate.enqueue_failed",
                person_id=str(person_id),
                exc_info=True,
            )

    return ProfilePictureJobResponse(
        job_id=job_id,
        person_id=person_id,
        mode=mode,  # type: ignore[arg-type]
        source="regenerate",
        enqueued=enqueued,
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
    person = await get_person_by_id(db_pool, person_id=person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")

    mode = "with_reference" if body.reference_s3_key else "no_reference"
    job_id = str(uuid4())
    gender_contract = map_gender(person.gender)
    image_prompt = compose_image_prompt(
        name=person.name,
        gender=person.gender,
        relationship=person.relationship,
        user_instructions=body.instructions,
    )

    enqueued = False
    if profile_picture_queue is not None:
        try:
            msg_id = await profile_picture_queue.push(
                job_id=job_id,
                person_id=person_id,
                mode=mode,
                image_prompt=image_prompt,
                source="edit",
                name=person.name,
                gender=gender_contract,
                relationship=person.relationship,
                reference_s3_key=body.reference_s3_key,
                user_prompt=body.instructions,
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning(
                "profile_picture.edit.enqueue_failed",
                person_id=str(person_id),
                exc_info=True,
            )

    return ProfilePictureJobResponse(
        job_id=job_id,
        person_id=person_id,
        mode=mode,  # type: ignore[arg-type]
        source="edit",
        enqueued=enqueued,
    )
