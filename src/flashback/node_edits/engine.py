"""Generic edit engine.

Top-level orchestration:

  1. Resolve :class:`NodeEditConfig` for ``node_type``.
  2. Read the prior active row + the legacy subject.
  3. Run the per-type edit-LLM call.
  4. Open ONE transaction. Dispatch to the strategy
     (:func:`apply_moment_edit` or :func:`apply_entity_edit`).
  5. Commit. Push embedding job(s) + the artifact job (if the
     registry says ``artifact_regen=True``).

Per CLAUDE.md invariant #4 we never compute embeddings inline; we
push to the ``embedding`` queue. Per s3 we never write the
``image_url`` / ``video_url`` columns; we push to
``artifact_generation`` and Node consumes it. Per invariant #5
supersession + edge repointing happen in the same transaction (the
strategies enforce this).
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from flashback.config import HttpConfig
from flashback.workers.extraction.persistence import (
    LLMProvenance,
    PersonRow,
)

from . import _async_sql as sql
from .llm import run_edit_llm
from .registry import REGISTRY, NodeEditConfig
from .schema import NodeEditResult
from .strategies import (
    ArtifactPushSpec,
    EditWriteResult,
    EmbeddingPushSpec,
    EntityEditLostUpdate,
    apply_entity_edit,
    apply_moment_edit,
)

log = structlog.get_logger("flashback.node_edits.engine")


class _EmbeddingPusher(Protocol):
    def __call__(
        self,
        *,
        record_type: str,
        record_id: str,
        source_text: str,
        embedding_model: str,
        embedding_model_version: str,
    ) -> Any: ...


class _ArtifactPusher(Protocol):
    def __call__(
        self,
        *,
        record_type: str,
        record_id: str,
        person_id: str,
        artifact_kind: str,
        generation_prompt: str,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Errors surfaced by the engine
# ---------------------------------------------------------------------------


class UnknownNodeType(ValueError):
    """Raised when the route's ``node_type`` is not registered."""

    def __init__(self, node_type: str) -> None:
        super().__init__(
            f"unknown node_type {node_type!r}; "
            f"valid: {sorted(REGISTRY)}"
        )
        self.node_type = node_type


class NodeNotFound(LookupError):
    """Active row matching (node_id, person_id) does not exist."""

    def __init__(self, *, node_type: str, node_id: str) -> None:
        super().__init__(
            f"active {node_type} {node_id} not found for the supplied person"
        )
        self.node_type = node_type
        self.node_id = node_id


class PersonNotFound(LookupError):
    """``persons`` row missing — the legacy subject is gone."""

    def __init__(self, person_id: str) -> None:
        super().__init__(f"person {person_id} not found")
        self.person_id = person_id


class EditLLMOutputInvalid(ValueError):
    """LLM tool args failed Pydantic validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def edit_node(
    *,
    node_type: str,
    node_id: str,
    person_id: str,
    free_text: str,
    db_pool: AsyncConnectionPool,
    cfg: HttpConfig,
    push_embedding: _EmbeddingPusher,
    push_artifact: _ArtifactPusher | None,
) -> NodeEditResult:
    """Run a single edit end-to-end. Async."""
    config = REGISTRY.get(node_type)
    if config is None:
        raise UnknownNodeType(node_type)

    if config.artifact_regen and push_artifact is None:
        raise RuntimeError(
            f"node_type {node_type!r} requires the artifact_generation "
            f"queue, but ARTIFACT_QUEUE_URL is not configured"
        )

    # 1. Read prior row + person.
    prior_row, person = await _load_prior(
        db_pool=db_pool,
        config=config,
        node_id=node_id,
        person_id=person_id,
    )

    # 2. Edit-LLM call (outside the transaction — it can take seconds).
    llm_args = await run_edit_llm(
        config=config,
        settings=cfg,
        provider=cfg.llm_node_edit_provider,
        model=cfg.llm_node_edit_model,
        timeout=cfg.llm_node_edit_timeout_seconds,
        max_tokens=cfg.llm_node_edit_max_tokens,
        subject_name=person.name,
        subject_relationship=None,
        prior_row=_redact_immutables(prior_row, config),
        edited_text=free_text,
    )

    provenance = LLMProvenance(
        provider=cfg.llm_node_edit_provider,
        model=cfg.llm_node_edit_model,
        prompt_version=config.prompt_version,
    )

    # 3. Apply the strategy in one transaction.
    write_result = await _apply_in_transaction(
        db_pool=db_pool,
        config=config,
        person=person,
        node_id=node_id,
        llm_args=llm_args,
        provenance=provenance,
    )

    # 4. Push fan-out jobs after commit.
    embedding_jobs_pushed = _push_embeddings(
        push_embedding,
        cfg=cfg,
        pushes=write_result.embedding_pushes,
    )
    artifact_queued = False
    if push_artifact is not None and write_result.artifact_pushes:
        artifact_queued = _push_artifacts(
            push_artifact,
            person_id=person_id,
            pushes=write_result.artifact_pushes,
        )

    log.info(
        "node_edits.edit_completed",
        node_type=node_type,
        node_id=node_id,
        new_node_id=write_result.node_id,
        superseded_id=write_result.superseded_id,
        new_entity_count=len(write_result.new_entity_ids),
        edges_added=write_result.edges_added,
        edges_removed=write_result.edges_removed,
        embedding_jobs_pushed=embedding_jobs_pushed,
        artifact_queued=artifact_queued,
    )

    return NodeEditResult(
        node_type=node_type,
        node_id=write_result.node_id,
        superseded_id=write_result.superseded_id,
        new_entity_ids=list(write_result.new_entity_ids),
        edges_added=write_result.edges_added,
        edges_removed=write_result.edges_removed,
        artifact_queued=artifact_queued,
        embedding_jobs_pushed=embedding_jobs_pushed,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _load_prior(
    *,
    db_pool: AsyncConnectionPool,
    config: NodeEditConfig,
    node_id: str,
    person_id: str,
) -> tuple[dict[str, Any], PersonRow]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            person = await sql.fetch_person_async(cur, person_id=person_id)
            if person is None:
                raise PersonNotFound(person_id)

            if config.node_type == "moment":
                row = await sql.fetch_active_moment_async(
                    cur, moment_id=node_id, person_id=person_id
                )
            elif config.node_type == "entity":
                row = await sql.fetch_active_entity_async(
                    cur, entity_id=node_id, person_id=person_id
                )
            else:  # pragma: no cover — registry-gated above
                raise UnknownNodeType(config.node_type)

            if row is None:
                raise NodeNotFound(
                    node_type=config.node_type, node_id=node_id
                )
            return row, person


async def _apply_in_transaction(
    *,
    db_pool: AsyncConnectionPool,
    config: NodeEditConfig,
    person: PersonRow,
    node_id: str,
    llm_args: dict[str, Any],
    provenance: LLMProvenance,
) -> EditWriteResult:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                if config.mutation_strategy == "supersede":
                    # Moments only in v1.
                    try:
                        return await apply_moment_edit(
                            cur,
                            config=config,
                            person=person,
                            old_moment_id=node_id,
                            llm_args=llm_args,
                            llm_provenance=provenance,
                        )
                    except ValidationError as exc:
                        raise EditLLMOutputInvalid(str(exc)) from exc
                if config.mutation_strategy == "in_place":
                    # Entities only in v1.
                    try:
                        return await apply_entity_edit(
                            cur,
                            config=config,
                            person=person,
                            entity_id=node_id,
                            llm_args=llm_args,
                            llm_provenance=provenance,
                        )
                    except EntityEditLostUpdate:
                        raise
                raise RuntimeError(
                    f"unknown mutation_strategy "
                    f"{config.mutation_strategy!r} on registry entry "
                    f"{config.node_type!r}"
                )


def _redact_immutables(
    row: dict[str, Any], config: NodeEditConfig
) -> dict[str, Any]:
    """Drop columns the LLM has no business seeing or rewriting.

    We strip ids and the technical immutable columns (status, embedding
    columns, URL columns, timestamps) before rendering the prior row in
    the prompt. The LLM still sees ``kind`` / ``name`` for entities so
    it can ground its rewrite, even though those are not in its tool
    schema and thus cannot be changed.
    """
    blocked = {
        "id",
        "person_id",
        "status",
        "superseded_by",
        "merged_into",
        "narrative_embedding",
        "description_embedding",
        "embedding_model",
        "embedding_model_version",
        "video_url",
        "image_url",
        "thumbnail_url",
        "created_at",
        "updated_at",
        "llm_provider",
        "llm_model",
        "prompt_version",
    }
    # registry's immutable_fields is the source of truth; merge.
    blocked |= set(config.immutable_fields)
    return {k: v for k, v in row.items() if k not in blocked or k in {"kind", "name"}}


def _push_embeddings(
    pusher: _EmbeddingPusher,
    *,
    cfg: HttpConfig,
    pushes: list[EmbeddingPushSpec],
) -> int:
    count = 0
    for spec in pushes:
        if not spec.source_text:
            continue
        pusher(
            record_type=spec.record_type,
            record_id=spec.record_id,
            source_text=spec.source_text,
            embedding_model=cfg.embedding_model,
            embedding_model_version=cfg.embedding_model_version,
        )
        count += 1
    return count


def _push_artifacts(
    pusher: _ArtifactPusher,
    *,
    person_id: str,
    pushes: list[ArtifactPushSpec],
) -> bool:
    pushed = False
    for spec in pushes:
        if not spec.generation_prompt:
            continue
        pusher(
            record_type=spec.record_type,
            record_id=spec.record_id,
            person_id=person_id,
            artifact_kind=spec.artifact_kind,
            generation_prompt=spec.generation_prompt,
        )
        pushed = True
    return pushed


__all__ = [
    "EditLLMOutputInvalid",
    "NodeNotFound",
    "PersonNotFound",
    "UnknownNodeType",
    "edit_node",
]
