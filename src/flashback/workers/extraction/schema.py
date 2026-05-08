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
    def _validate_moment_indexes(self) -> "ExtractionResult":
        """Bounds-check entity / trait indexes referenced by moments."""
        n_entities = len(self.entities)
        n_traits = len(self.traits)
        for m_idx, moment in enumerate(self.moments):
            for i in moment.involves_entity_indexes:
                if not (0 <= i < n_entities):
                    raise ValueError(
                        f"moments[{m_idx}].involves_entity_indexes contains "
                        f"out-of-range index {i} (entities length={n_entities})"
                    )
            if (
                moment.happened_at_entity_index is not None
                and not (0 <= moment.happened_at_entity_index < n_entities)
            ):
                raise ValueError(
                    f"moments[{m_idx}].happened_at_entity_index {moment.happened_at_entity_index} "
                    f"out of range (entities length={n_entities})"
                )
            for i in moment.exemplifies_trait_indexes:
                if not (0 <= i < n_traits):
                    raise ValueError(
                        f"moments[{m_idx}].exemplifies_trait_indexes contains "
                        f"out-of-range index {i} (traits length={n_traits})"
                    )
        for e_idx, entity in enumerate(self.entities):
            for i in entity.related_to_entity_indexes:
                if not (0 <= i < n_entities):
                    raise ValueError(
                        f"entities[{e_idx}].related_to_entity_indexes contains "
                        f"out-of-range index {i} (entities length={n_entities})"
                    )
                if i == e_idx:
                    raise ValueError(
                        f"entities[{e_idx}].related_to_entity_indexes references self"
                    )
        return self


# ---------------------------------------------------------------------------
# Inbound queue payload
# ---------------------------------------------------------------------------


class SegmentTurn(BaseModel):
    """One turn from the closed segment as it arrives from the queue."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str
    timestamp: str  # kept as string; ordering is positional in the list


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
    contributor_display_name: str = ""
