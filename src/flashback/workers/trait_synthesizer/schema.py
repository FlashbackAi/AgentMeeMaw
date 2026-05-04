"""Pydantic and dataclass models for the Trait Synthesizer.

Three surfaces:

* :class:`TraitSynthMessage` — body of one ``trait_synthesizer`` SQS
  message. Carries only the ``person_id``; the worker rebuilds context
  from the canonical graph at processing time so retried messages
  don't operate on stale facts.
* :class:`TraitSynthesisResult` and helpers
  (:class:`ExistingTraitDecision`, :class:`NewTraitProposal`) — the
  parsed output of the single LLM call.
* Context views (:class:`ExistingTraitView`, :class:`ThreadView`,
  :class:`TraitSynthContext`) — what :mod:`context` builds from the DB
  and feeds into the LLM call.

The strength ladder is encoded as a Literal so the LLM tool schema's
enum and the persistence-layer ladder logic share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Inbound queue payload
# ---------------------------------------------------------------------------


class TraitSynthMessage(BaseModel):
    """Body of one ``trait_synthesizer`` SQS message."""

    model_config = ConfigDict(extra="ignore")

    person_id: UUID


# ---------------------------------------------------------------------------
# Strength ladder + action enums
# ---------------------------------------------------------------------------


Strength = Literal["mentioned_once", "moderate", "strong", "defining"]
Action = Literal["keep", "upgrade", "downgrade"]

STRENGTH_LADDER: tuple[Strength, ...] = (
    "mentioned_once",
    "moderate",
    "strong",
    "defining",
)


# ---------------------------------------------------------------------------
# LLM-tool result shapes
# ---------------------------------------------------------------------------


class ExistingTraitDecision(BaseModel):
    """The model's decision for one EXISTING trait.

    ``supporting_thread_ids`` is required for ``upgrade``/``downgrade``
    and is the source of the new ``thread → trait`` ``evidences`` edges
    written in the persistence layer. For ``keep``, the field is
    ignored.
    """

    model_config = ConfigDict(extra="forbid")

    trait_id: UUID
    action: Action
    reasoning: str
    supporting_thread_ids: list[UUID] = Field(default_factory=list)


class NewTraitProposal(BaseModel):
    """A new trait proposed by the model."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)
    description: str
    initial_strength: Strength
    supporting_thread_ids: list[UUID] = Field(min_length=1)
    reasoning: str


class TraitSynthesisResult(BaseModel):
    """Parsed ``synthesize_traits`` tool arguments."""

    model_config = ConfigDict(extra="forbid")

    existing_trait_decisions: list[ExistingTraitDecision]
    new_trait_proposals: list[NewTraitProposal]
    overall_reasoning: str


# ---------------------------------------------------------------------------
# Context views (DB → LLM)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingTraitView:
    """One active trait, plus how many active moments currently link to it.

    ``moment_count`` is the count of ``exemplifies`` edges from active
    moments to this trait. It's a useful signal for the model when
    deciding upgrades — "this trait is already supported by N moments
    and the threads add more weight on top of that".
    """

    id: str
    name: str
    description: str | None
    strength: Strength
    moment_count: int


@dataclass(frozen=True)
class ThreadView:
    """One active thread with its supporting active-moment count."""

    id: str
    name: str
    description: str
    moment_count: int


@dataclass(frozen=True)
class TraitSynthContext:
    """Everything the LLM needs for one person."""

    person_id: str
    person_name: str
    existing_traits: list[ExistingTraitView]
    threads: list[ThreadView]
