"""
Pydantic models for the Extraction Worker.

Two surfaces:

* :class:`ExtractionResult` — the parsed shape of the Sonnet ``extract_segment``
  tool call. Mirrors ``EXTRACTION_TOOL.input_schema`` in
  :mod:`flashback.workers.extraction.prompts`. The drift-detector test in
  ``tests/workers/extraction/test_prompts.py`` keeps the JSON Schema and
  this Pydantic model honest with each other.

* :class:`ExtractionMessage` — the queue payload dropped on the
  ``extraction`` SQS queue by ``flashback.queues.extraction``. Used by the
  worker's sync SQS client to type-check inbound bodies.

The compatibility-check tool returns one of three string verdicts; we
keep that as a plain ``Literal`` rather than a model.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CompatibilityVerdict = Literal["refinement", "contradiction", "independent"]


# ---------------------------------------------------------------------------
# Extraction tool output
# ---------------------------------------------------------------------------


class TimeAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int | None = None
    decade: str | None = None
    life_period: str | None = None
    era: str | None = None

    def is_set(self) -> bool:
        """A time anchor is "set" if any field is populated."""
        return any(
            v is not None and v != ""
            for v in (self.year, self.decade, self.life_period, self.era)
        )


class ExtractedMoment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=120)
    narrative: str
    generation_prompt: str

    time_anchor: TimeAnchor | None = None
    life_period_estimate: str | None = None
    sensory_details: str | None = None
    emotional_tone: str | None = None
    contributor_perspective: str | None = None

    involves_entity_indexes: list[int] = Field(default_factory=list)
    happened_at_entity_index: int | None = None
    exemplifies_trait_indexes: list[int] = Field(default_factory=list)

    themes: list[str] = Field(default_factory=list)
    """Theme slugs this moment is "about" — drawn from the catalog passed
    to the extraction prompt (universal slugs + active emergent slugs for
    this person). Multi-tag allowed and expected: a wedding moment may
    carry both 'family' and 'milestones'. Unknown slugs are dropped at
    persistence time."""


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["person", "place", "object", "organization"]
    name: str
    generation_prompt: str

    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    attributes: dict = Field(default_factory=dict)
    related_to_entity_indexes: list[int] = Field(default_factory=list)


class ExtractedTrait(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None


class DroppedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dropped_phrase: str
    question_text: str
    themes: list[str] = Field(min_length=1)


class ExtractionResult(BaseModel):
    """
    Parsed ``extract_segment`` tool arguments.

    The LLM returns indexes into the ``entities`` and ``traits`` arrays
    rather than UUIDs, because UUIDs do not exist until the persistence
    layer inserts the rows. The persistence code resolves indexes to
    UUIDs after inserting entities and traits.
    """

    model_config = ConfigDict(extra="forbid")

    moments: list[ExtractedMoment] = Field(default_factory=list, max_length=3)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    traits: list[ExtractedTrait] = Field(default_factory=list)
    dropped_references: list[DroppedReference] = Field(
        default_factory=list, max_length=3
    )
    extraction_notes: str = ""

    @field_validator("dropped_references")
    @classmethod
    def _ensure_themes(
        cls, v: list[DroppedReference]
    ) -> list[DroppedReference]:
        """Invariant #9: every producer-emitted question carries ``themes``."""
        for dr in v:
            if not dr.themes:
                raise ValueError(
                    "dropped_references[].themes must contain at least one entry"
                )
        return v

    @model_validator(mode="after")
    def _sanitize_moment_indexes(self) -> "ExtractionResult":
        """Drop dangling / self-referential index edges instead of rejecting
        the whole extraction.

        A truncated LLM tool-call (output hitting ``max_tokens`` mid-array)
        emits moments that reference entity indexes which never got generated,
        producing e.g. ``involves_entity_indexes=[3]`` while only 3 entities
        landed. Raising here discarded the *entire* segment — every salvageable
        moment, entity, and trait — which violates invariant #6 (under-extract;
        drop low-confidence material, don't fail the batch) and is what sent
        content-dense legacies to the DLQ.

        We instead drop the out-of-range / self-referential edges and keep the
        valid rows, mirroring :func:`drop_orphan_traits`' handling of trait
        indexes. The ``max_tokens`` bump and the truncation log in
        ``flashback.llm.interface`` reduce how often this fires; this is the
        backstop that keeps a partial extraction from being lost.
        """
        n_entities = len(self.entities)
        n_traits = len(self.traits)
        for moment in self.moments:
            moment.involves_entity_indexes = [
                i for i in moment.involves_entity_indexes if 0 <= i < n_entities
            ]
            if moment.happened_at_entity_index is not None and not (
                0 <= moment.happened_at_entity_index < n_entities
            ):
                moment.happened_at_entity_index = None
            moment.exemplifies_trait_indexes = [
                i for i in moment.exemplifies_trait_indexes if 0 <= i < n_traits
            ]
        for e_idx, entity in enumerate(self.entities):
            entity.related_to_entity_indexes = [
                i
                for i in entity.related_to_entity_indexes
                if 0 <= i < n_entities and i != e_idx
            ]
        return self


def drop_orphan_traits(
    result: "ExtractionResult",
) -> tuple["ExtractionResult", int]:
    """Drop traits not referenced by any moment via ``exemplifies_trait_indexes``.

    Backstops invariant #18: a trait must be exemplified by ≥1 moment in
    the same extraction. The LLM is instructed to comply via the prompt;
    this filter catches drift. Returns the filtered result plus the count
    of traits dropped.

    Moment ``exemplifies_trait_indexes`` are remapped to the surviving
    traits' new positions. Out-of-range indexes (defense against malformed
    LLM output) are discarded silently.
    """
    if not result.traits:
        return result, 0

    referenced: set[int] = set()
    for moment in result.moments:
        for idx in moment.exemplifies_trait_indexes:
            if 0 <= idx < len(result.traits):
                referenced.add(idx)

    if len(referenced) == len(result.traits):
        return result, 0

    remap: dict[int, int] = {}
    new_traits: list[ExtractedTrait] = []
    for old_idx, trait in enumerate(result.traits):
        if old_idx in referenced:
            remap[old_idx] = len(new_traits)
            new_traits.append(trait)

    new_moments: list[ExtractedMoment] = [
        moment.model_copy(
            update={
                "exemplifies_trait_indexes": [
                    remap[i] for i in moment.exemplifies_trait_indexes if i in remap
                ],
            }
        )
        for moment in result.moments
    ]

    dropped = len(result.traits) - len(new_traits)
    return (
        result.model_copy(update={"traits": new_traits, "moments": new_moments}),
        dropped,
    )


# ---------------------------------------------------------------------------
# Inbound queue payload
# ---------------------------------------------------------------------------


class SegmentTurn(BaseModel):
    """One turn from the closed segment as it arrives from the queue."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str
    timestamp: str  # kept as string; ordering is positional in the list
    metadata: dict = Field(default_factory=dict)


class ExtractionMessage(BaseModel):
    """
    Parsed ``extraction`` queue body.

    Mirrors :class:`flashback.queues.extraction.ExtractionQueueProducer.push`'s
    payload shape one-for-one.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: UUID
    person_id: UUID
    segment_turns: list[SegmentTurn]
    rolling_summary: str = ""
    prior_rolling_summary: str = ""
    seeded_question_id: UUID | None = None
    candidate_question_ids: list[UUID] = Field(default_factory=list)
    contributor_display_name: str = ""
