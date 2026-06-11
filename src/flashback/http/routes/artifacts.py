"""Generic artifact regenerate / edit endpoints for moments, entities, threads.

The profile-picture endpoint (``/persons/{id}/profile-picture[/edit]``) stays
separate because portraits use a fixed compositional recipe. This module
covers the LLM-emitted scene artifacts: a moment image / video, an entity
image, or a thread image.

Edit-history persistence sits on Node (Dynamo). The agent stays stateless:
Node sends ``prior_instructions`` on every edit call; we compose them in
order with the row's stored ``generation_prompt`` and the chosen preset.

Reference-image upload is allowed for ``moment`` and ``entity`` (Node uploads
to S3, sends the key here, we forward it to the artifact_generation queue).
Threads reject it — they're abstract arcs, not a visual subject.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.artifacts import (
    SCENE_NEGATIVE_PROMPT,
    build_generation_context,
    compose_scene_prompt,
    list_presets,
    write_latest_generation_context_async,
)
from flashback.artifacts.presets import resolve_preset
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_artifact_generation_queue,
    get_db_pool,
)
from flashback.http.models import (
    ArtifactEditRequest,
    ArtifactJobResponse,
    ArtifactPresetOut,
    ArtifactPresetsResponse,
    ArtifactRecordType,
    ArtifactRegenerateRequest,
)

if TYPE_CHECKING:
    from flashback.queues.artifact_generation import (
        ArtifactGenerationQueueProducer,
    )


router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.artifacts")


# record_type → (table, artifact_kind). Threads have no `person_id` column
# on every schema version, so the ownership check is by id only there.
_RECORD_CONFIG: dict[str, tuple[str, str]] = {
    "moment": ("moments", "video"),
    "entity": ("entities", "image"),
    "thread": ("threads", "image"),
}

_REFERENCE_ALLOWED: frozenset[str] = frozenset({"moment", "entity"})


@router.get("/artifact-presets", response_model=ArtifactPresetsResponse)
async def get_artifact_presets() -> ArtifactPresetsResponse:
    """Return the public preset registry. Default first."""
    return ArtifactPresetsResponse(
        presets=[ArtifactPresetOut(**p) for p in list_presets()],
    )


@router.post(
    "/artifacts/{record_type}/{record_id}/regenerate",
    response_model=ArtifactJobResponse,
)
async def regenerate_artifact(
    record_type: ArtifactRecordType,
    record_id: UUID,
    body: ArtifactRegenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> ArtifactJobResponse:
    """Re-compose the prompt with the chosen preset + optional reference, enqueue."""
    return await _enqueue_artifact_job(
        record_type=record_type,
        record_id=record_id,
        person_id=body.person_id,
        preset_input=body.preset,
        reference_s3_key=body.reference_s3_key,
        instructions=None,
        prior_instructions=[],
        source="regenerate",
        db_pool=db_pool,
        artifact_queue=artifact_queue,
    )


@router.post(
    "/artifacts/{record_type}/{record_id}/edit",
    response_model=ArtifactJobResponse,
)
async def edit_artifact(
    record_type: ArtifactRecordType,
    record_id: UUID,
    body: ArtifactEditRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> ArtifactJobResponse:
    """Stack ``prior_instructions`` + new ``instructions`` onto the base prompt, enqueue."""
    return await _enqueue_artifact_job(
        record_type=record_type,
        record_id=record_id,
        person_id=body.person_id,
        preset_input=body.preset,
        reference_s3_key=body.reference_s3_key,
        instructions=body.instructions,
        prior_instructions=body.prior_instructions,
        source="edit",
        db_pool=db_pool,
        artifact_queue=artifact_queue,
    )


async def _enqueue_artifact_job(
    *,
    record_type: str,
    record_id: UUID,
    person_id: UUID,
    preset_input: str | None,
    reference_s3_key: str | None,
    instructions: str | None,
    prior_instructions: list[str],
    source: str,
    db_pool: AsyncConnectionPool,
    artifact_queue: "ArtifactGenerationQueueProducer | None",
) -> ArtifactJobResponse:
    if record_type not in _RECORD_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported record_type: {record_type!r}",
        )
    if reference_s3_key and record_type not in _REFERENCE_ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"reference_s3_key is not supported for record_type "
                f"{record_type!r}; only {sorted(_REFERENCE_ALLOWED)} accept "
                f"reference uploads"
            ),
        )

    try:
        preset_slug = resolve_preset(preset_input)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    base_prompt = await _fetch_active_generation_prompt(
        db_pool=db_pool,
        record_type=record_type,
        record_id=record_id,
        person_id=person_id,
    )
    if base_prompt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{record_type} not found for this person, or has no generation_prompt",
        )

    ground_truth = await fetch_ground_truth(db_pool, person_id)
    composed_prompt = compose_scene_prompt(
        base_prompt=base_prompt,
        prior_instructions=prior_instructions,
        instructions=instructions,
        preset=preset_slug,
        ground_truth_context=render_ground_truth_block(ground_truth, "scene")
        or None,
    )

    table_name, artifact_kind = _RECORD_CONFIG[record_type]
    mode = "with_reference" if reference_s3_key else "no_reference"
    job_id = str(uuid4())
    context = build_generation_context(
        prompt=composed_prompt,
        negative_prompt=SCENE_NEGATIVE_PROMPT,
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
                table=table_name,
                record_id=record_id,
                context=context,
            )

    enqueued = False
    if artifact_queue is not None:
        try:
            msg_id = await artifact_queue.push(
                job_id=job_id,
                record_type=record_type,
                record_id=str(record_id),
                person_id=str(person_id),
                artifact_kind=artifact_kind,
                source=source,
                composed_at=context["composed_at"],
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning(
                "artifacts.enqueue_failed",
                record_type=record_type,
                record_id=str(record_id),
                person_id=str(person_id),
                source=source,
                exc_info=True,
            )

    return ArtifactJobResponse(
        job_id=job_id,
        record_type=record_type,  # type: ignore[arg-type]
        record_id=record_id,
        person_id=person_id,
        artifact_kind=artifact_kind,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        preset=preset_slug,
        enqueued=enqueued,
    )


async def _fetch_active_generation_prompt(
    *,
    db_pool: AsyncConnectionPool,
    record_type: str,
    record_id: UUID,
    person_id: UUID,
) -> str | None:
    """Return the row's current ``generation_prompt`` if active + owned, else None.

    All three tables carry ``person_id`` and ``status`` columns per SCHEMA.md;
    we filter on both (invariant #1, #2) and require a non-null prompt.
    """
    table = _RECORD_CONFIG[record_type][0]
    query = (
        f"SELECT generation_prompt FROM {table} "
        f"WHERE id = %s AND person_id = %s AND status = 'active' "
        f"AND generation_prompt IS NOT NULL "
        f"LIMIT 1"
    )
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, (str(record_id), str(person_id)))
            row = await cur.fetchone()
    if row is None:
        return None
    return row[0]
