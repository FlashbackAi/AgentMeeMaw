"""Per-type persistence strategies for node_edits.

The engine dispatches on :class:`NodeEditConfig.mutation_strategy`:

* ``supersede`` — moments. Insert a new moment row, flip the old to
  ``superseded``, repoint inbound edges, drop the old outbound edges,
  and emit the new outbound ``involves`` / ``happened_at`` edges from
  the LLM-supplied entities. Mirrors the extraction worker's persist
  pipeline for a single moment with a known ``supersedes_id``.

* ``in_place`` — entities. UPDATE the row's content fields, clear the
  embedding columns, and let the caller push a fresh embedding job.
  Mirrors the identity-merge survivor-update pattern in
  ``flashback.identity_merges.repository._merge_entity_rows``.

Each strategy returns an :class:`EditWriteResult` describing what
changed: the canonical id (new for moments, same for entities), any
new entity ids, edge counts, and the post-commit follow-ups the engine
must push (embedding jobs + an artifact job per
:class:`NodeEditConfig.artifact_regen`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import ValidationError

from flashback.workers.extraction.persistence import (
    LLMProvenance,
    PersonRow,
)
from flashback.workers.extraction.schema import ExtractedEntity, ExtractedMoment

from . import _async_sql as sql
from .registry import NodeEditConfig

log = structlog.get_logger("flashback.node_edits.strategies")


@dataclass(frozen=True)
class EmbeddingPushSpec:
    """One pending embedding-queue push."""

    record_type: str
    record_id: str
    source_text: str


@dataclass(frozen=True)
class ArtifactPushSpec:
    """One pending artifact-queue push."""

    record_type: str
    record_id: str
    artifact_kind: str
    generation_prompt: str


@dataclass
class EditWriteResult:
    """What a strategy commits + what the engine still needs to push."""

    node_id: str
    superseded_id: str | None = None
    new_entity_ids: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0
    embedding_pushes: list[EmbeddingPushSpec] = field(default_factory=list)
    artifact_pushes: list[ArtifactPushSpec] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Moment edit strategy
# ---------------------------------------------------------------------------


async def apply_moment_edit(
    cur,
    *,
    config: NodeEditConfig,
    person: PersonRow,
    old_moment_id: str,
    llm_args: dict[str, Any],
    llm_provenance: LLMProvenance,
) -> EditWriteResult:
    """Persist a moment edit. Caller owns the transaction.

    Steps:

      1. Parse LLM args into :class:`ExtractedMoment` + entity list.
      2. Apply subject guard.
      3. Insert new entities; build index map.
      4. Insert the new moment row.
      5. Supersede the old moment (flip status, repoint inbound edges,
         drop outbound).
      6. Insert fresh outbound moment edges (involves / happened_at).
      7. Run identity-merge suggestion scan over the new entities.

    Returns the spec of what to push post-commit.
    """
    moment_data, entity_data = _parse_moment_llm_args(llm_args)

    surviving, dropped = sql.apply_subject_guard_pure(
        person=person, entities=entity_data
    )

    entity_ids = await sql.insert_entities_async(
        cur,
        person_id=person.id,
        entities=surviving,
        llm_provenance=llm_provenance,
    )

    entity_index_to_id = sql.build_entity_index_map(
        original_entities=entity_data,
        surviving_entities=surviving,
        surviving_ids=entity_ids,
    )

    new_moment_id = await sql.insert_moment_async(
        cur,
        person_id=person.id,
        moment=moment_data,
        llm_provenance=llm_provenance,
    )

    supersede_counts = await sql.supersede_moment_async(
        cur,
        old_moment_id=old_moment_id,
        new_moment_id=new_moment_id,
    )

    edges_added = await sql.insert_moment_edges_async(
        cur,
        moment_id=new_moment_id,
        moment=moment_data,
        entity_index_to_id=entity_index_to_id,
        entity_kinds=[e.kind for e in surviving],
    )

    await sql.create_entity_merge_suggestions_async(
        cur,
        person_id=person.id,
        target_entity_ids=entity_ids,
    )

    embedding_pushes: list[EmbeddingPushSpec] = [
        EmbeddingPushSpec(
            record_type="moment",
            record_id=new_moment_id,
            source_text=moment_data.narrative,
        )
    ]
    for new_entity_id, entity in zip(entity_ids, surviving):
        if entity.description:
            embedding_pushes.append(
                EmbeddingPushSpec(
                    record_type="entity",
                    record_id=new_entity_id,
                    source_text=entity.description,
                )
            )

    artifact_pushes: list[ArtifactPushSpec] = []
    if config.artifact_regen and config.artifact_kind is not None:
        artifact_pushes.append(
            ArtifactPushSpec(
                record_type="moment",
                record_id=new_moment_id,
                artifact_kind=config.artifact_kind,
                generation_prompt=moment_data.generation_prompt,
            )
        )
        for new_entity_id, entity in zip(entity_ids, surviving):
            artifact_pushes.append(
                ArtifactPushSpec(
                    record_type="entity",
                    record_id=new_entity_id,
                    artifact_kind="image",
                    generation_prompt=entity.generation_prompt,
                )
            )

    log.info(
        "node_edits.moment_edit_committed",
        old_moment_id=old_moment_id,
        new_moment_id=new_moment_id,
        new_entity_count=len(entity_ids),
        dropped_subject_self_references=dropped,
        edges_added=edges_added,
        edges_repointed=supersede_counts.inbound_repointed,
        edges_removed=supersede_counts.outbound_deleted,
    )

    return EditWriteResult(
        node_id=new_moment_id,
        superseded_id=old_moment_id,
        new_entity_ids=entity_ids,
        edges_added=edges_added,
        edges_removed=supersede_counts.outbound_deleted,
        embedding_pushes=embedding_pushes,
        artifact_pushes=artifact_pushes,
    )


def _parse_moment_llm_args(
    args: dict[str, Any],
) -> tuple[ExtractedMoment, list[ExtractedEntity]]:
    """Validate the moment-edit LLM tool args into typed models.

    Reuses :class:`ExtractedMoment` / :class:`ExtractedEntity` from the
    extraction worker — same shapes, same validation. Raises
    :class:`pydantic.ValidationError` on bad shape; the engine surfaces
    that as a 502 (LLM produced something we can't persist).
    """
    raw_entities = args.get("entities") or []
    entities: list[ExtractedEntity] = []
    for raw in raw_entities:
        entities.append(ExtractedEntity.model_validate(raw))

    moment_args = {k: v for k, v in args.items() if k != "entities"}
    moment_args.setdefault("involves_entity_indexes", [])
    moment_args.setdefault("exemplifies_trait_indexes", [])
    moment = ExtractedMoment.model_validate(moment_args)

    # Bounds-check entity-index references — ExtractionResult does this
    # at the result-level model_validator, but here we have a single
    # moment so re-implement the relevant bits inline.
    n_entities = len(entities)
    for i in moment.involves_entity_indexes:
        if not (0 <= i < n_entities):
            raise ValidationError.from_exception_data(  # type: ignore[arg-type]
                "ExtractedMoment",
                [
                    {
                        "type": "value_error",
                        "loc": ("involves_entity_indexes", i),
                        "msg": (
                            f"index {i} out of range "
                            f"(entities length={n_entities})"
                        ),
                        "input": i,
                    }
                ],
            )
    if moment.happened_at_entity_index is not None and not (
        0 <= moment.happened_at_entity_index < n_entities
    ):
        raise ValidationError.from_exception_data(  # type: ignore[arg-type]
            "ExtractedMoment",
            [
                {
                    "type": "value_error",
                    "loc": ("happened_at_entity_index",),
                    "msg": (
                        f"index {moment.happened_at_entity_index} "
                        f"out of range (entities length={n_entities})"
                    ),
                    "input": moment.happened_at_entity_index,
                }
            ],
        )
    return moment, entities


# ---------------------------------------------------------------------------
# Entity edit strategy
# ---------------------------------------------------------------------------


async def apply_entity_edit(
    cur,
    *,
    config: NodeEditConfig,
    person: PersonRow,
    entity_id: str,
    llm_args: dict[str, Any],
    llm_provenance: LLMProvenance,
) -> EditWriteResult:
    """Persist an entity edit in place.

    Updates ``description`` / ``aliases`` / ``attributes`` /
    ``generation_prompt``, clears embedding columns, returns the spec
    of follow-up queue pushes. The entity's identity-defining fields
    (``id``, ``kind``, ``name``) are not touched.
    """
    description = _str_or_none(llm_args.get("description"))
    aliases = _string_list(llm_args.get("aliases"))
    attributes = llm_args.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    generation_prompt = _str_or_none(llm_args.get("generation_prompt"))

    updated = await sql.update_entity_in_place_async(
        cur,
        entity_id=entity_id,
        person_id=person.id,
        description=description,
        aliases=aliases,
        attributes=attributes,
        generation_prompt=generation_prompt,
        llm_provenance=llm_provenance,
    )
    if not updated:
        # Lost-update guard: the row was superseded between our read
        # and our write (e.g. by a concurrent merge approval). The
        # engine raises a 409 from this empty-result path.
        raise EntityEditLostUpdate(entity_id=entity_id)

    embedding_pushes: list[EmbeddingPushSpec] = []
    if description:
        embedding_pushes.append(
            EmbeddingPushSpec(
                record_type="entity",
                record_id=entity_id,
                source_text=description,
            )
        )

    artifact_pushes: list[ArtifactPushSpec] = []
    if (
        config.artifact_regen
        and config.artifact_kind is not None
        and generation_prompt
    ):
        artifact_pushes.append(
            ArtifactPushSpec(
                record_type="entity",
                record_id=entity_id,
                artifact_kind=config.artifact_kind,
                generation_prompt=generation_prompt,
            )
        )

    log.info(
        "node_edits.entity_edit_committed",
        entity_id=entity_id,
        person_id=person.id,
        artifact_queued=bool(artifact_pushes),
    )

    return EditWriteResult(
        node_id=entity_id,
        embedding_pushes=embedding_pushes,
        artifact_pushes=artifact_pushes,
    )


class EntityEditLostUpdate(RuntimeError):
    """Raised when the entity row is no longer active under (id, person_id).

    Translated to 409 by the HTTP route. Surfaced to the user as
    "this entity was changed concurrently; refresh and try again".
    """

    def __init__(self, *, entity_id: str) -> None:
        super().__init__(f"entity {entity_id} no longer active for this person")
        self.entity_id = entity_id


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s:
            out.append(s)
    return out


__all__ = [
    "ArtifactPushSpec",
    "EditWriteResult",
    "EmbeddingPushSpec",
    "EntityEditLostUpdate",
    "apply_entity_edit",
    "apply_moment_edit",
]
