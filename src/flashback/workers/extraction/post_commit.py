"""
Post-commit fan-out: embedding and artifact queue pushes.

Both pushes happen *after* the persistence transaction commits, because
both are external SQS calls. A failure here doesn't affect graph
correctness — embedding rows simply stay with NULL vectors until a
backfill catches them, and artifact rows stay with NULL URLs until the
next push (manual rerun).

Embedding-push policy: emit one job per inserted row that carries a
non-empty source text. The embedding worker drains the queue, calls
Voyage, and writes the vector + identity stamps under invariant #3.

Artifact-push policy: emit one job per inserted row that carries a
non-empty ``generation_prompt``. Person artifacts are NOT pushed here —
person rows are created by Node, not the agent — but moments and
entities are.
"""

from __future__ import annotations

import structlog

from .schema import ExtractedEntity, ExtractedMoment, ExtractionResult
from .sqs_client import ArtifactJobSender, EmbeddingJobSender

log = structlog.get_logger("flashback.workers.extraction.post_commit")


def push_embedding_jobs(
    *,
    sender: EmbeddingJobSender,
    extraction: ExtractionResult,
    moment_ids: list[str],
    surviving_entities: list[ExtractedEntity],
    entity_ids: list[str],
    trait_ids: list[str],
    question_ids: list[str],
    embedding_model: str,
    embedding_model_version: str,
) -> int:
    """
    Push one embedding job per embedded row. Returns the count pushed.

    Body shape mirrors the existing embedding worker's expected payload
    so the consumer drains it without modification.
    """
    pushed = 0
    pushed += _push_moment_embeddings(
        sender=sender,
        moments=extraction.moments,
        moment_ids=moment_ids,
        embedding_model=embedding_model,
        embedding_model_version=embedding_model_version,
    )

    if len(surviving_entities) != len(entity_ids):
        raise ValueError(
            "push_embedding_jobs: surviving_entities and entity_ids must "
            "have matching lengths"
        )
    for entity, eid in zip(surviving_entities, entity_ids, strict=True):
        if not entity.description:
            continue
        sender.send(
            record_type="entity",
            record_id=eid,
            source_text=entity.description,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
        pushed += 1

    if len(extraction.traits) != len(trait_ids):
        raise ValueError(
            "push_embedding_jobs: traits and trait_ids must have matching lengths"
        )
    for trait, tid in zip(extraction.traits, trait_ids, strict=True):
        source_text = trait.name
        if trait.description:
            source_text = f"{trait.name}, {trait.description}"
        sender.send(
            record_type="trait",
            record_id=tid,
            source_text=source_text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
        pushed += 1

    if len(extraction.dropped_references) != len(question_ids):
        raise ValueError(
            "push_embedding_jobs: dropped_references and question_ids must "
            "have matching lengths"
        )
    for dr, qid in zip(extraction.dropped_references, question_ids, strict=True):
        sender.send(
            record_type="question",
            record_id=qid,
            source_text=dr.question_text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
        pushed += 1

    log.info("post_commit.embedding_jobs_pushed", count=pushed)
    return pushed


def push_artifact_jobs(
    *,
    sender: ArtifactJobSender,
    person_id: str,
    moments: list[ExtractedMoment],
    moment_ids: list[str],
    surviving_entities: list[ExtractedEntity],
    entity_ids: list[str],
) -> int:
    """
    Push artifact jobs (image for entities, video for moments).

    Threads aren't created by the extraction worker — the Thread Detector
    (step 14) emits artifact jobs for threads it produces.
    """
    pushed = 0
    if len(moments) != len(moment_ids):
        raise ValueError(
            "push_artifact_jobs: moments and moment_ids must have matching lengths"
        )
    for moment, mid in zip(moments, moment_ids, strict=True):
        if not moment.generation_prompt:
            continue
        sender.send(
            record_type="moment",
            record_id=mid,
            person_id=person_id,
            artifact_kind="video",
            generation_prompt=moment.generation_prompt,
        )
        pushed += 1

    if len(surviving_entities) != len(entity_ids):
        raise ValueError(
            "push_artifact_jobs: surviving_entities and entity_ids must "
            "have matching lengths"
        )
    for entity, eid in zip(surviving_entities, entity_ids, strict=True):
        if not entity.generation_prompt:
            continue
        sender.send(
            record_type="entity",
            record_id=eid,
            person_id=person_id,
            artifact_kind="image",
            generation_prompt=entity.generation_prompt,
        )
        pushed += 1

    log.info("post_commit.artifact_jobs_pushed", count=pushed)
    return pushed


def _push_moment_embeddings(
    *,
    sender: EmbeddingJobSender,
    moments: list[ExtractedMoment],
    moment_ids: list[str],
    embedding_model: str,
    embedding_model_version: str,
) -> int:
    if len(moments) != len(moment_ids):
        raise ValueError(
            "push_embedding_jobs: moments and moment_ids must have matching lengths"
        )
    pushed = 0
    for moment, mid in zip(moments, moment_ids, strict=True):
        if not moment.narrative:
            continue
        sender.send(
            record_type="moment",
            record_id=mid,
            source_text=moment.narrative,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
        pushed += 1
    return pushed
