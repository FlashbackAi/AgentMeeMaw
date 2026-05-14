"""Pydantic and dataclass models for the Profile Summary Generator.

Three surfaces:

* :class:`ProfileSummaryMessage` — body of one ``profile_summary`` SQS
  message. Carries only the ``person_id``; the worker rebuilds context
  from the canonical graph at processing time so retried messages
  always operate on current state.
* Context views (:class:`TraitView`, :class:`ThreadView`,
  :class:`EntityView`, :class:`TimePeriodView`,
  :class:`ProfileSummaryContext`) — what :mod:`context` builds from
  the DB and feeds into the LLM call.

Unlike the Trait Synthesizer, the LLM output here is plain prose, so
there is no parsed-result schema — the returned string IS the summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Inbound queue payload
# ---------------------------------------------------------------------------


class ProfileSummaryMessage(BaseModel):
    """Body of one ``profile_summary`` SQS message."""

    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    session_id: UUID | None = None
    idempotency_key: str | None = None
    contributor_display_name: str = ""


# ---------------------------------------------------------------------------
# Strength + entity-kind enums
# ---------------------------------------------------------------------------


Strength = Literal["mentioned_once", "moderate", "strong", "defining"]
EntityKind = Literal["person", "place", "object", "organization"]


# ---------------------------------------------------------------------------
# Context views (DB → LLM)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraitView:
    """One active trait with its strength.

    Description may be NULL on legacy rows; the renderer handles that.
    """

    name: str
    description: str | None
    strength: Strength


@dataclass(frozen=True)
class ThreadView:
    """One active thread with its evidencing-moment count."""

    name: str
    description: str
    moment_count: int


@dataclass(frozen=True)
class EntityView:
    """One active entity with its mention count.

    Mention count is ``involves`` edges from active moments. Entities
    with zero active-moment mentions are filtered out upstream.
    """

    kind: EntityKind
    name: str
    description: str | None
    mention_count: int


@dataclass(frozen=True)
class TimePeriodView:
    """Derived in code, not LLM.

    ``year_range`` is None when no active moments have a non-null
    ``time_anchor.year``. ``life_periods`` is sorted by approximate
    chronology (see :mod:`time_period`).
    """

    year_range: tuple[int, int] | None
    life_periods: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileSummaryContext:
    """Everything the LLM needs for one person."""

    person_id: str
    person_name: str
    relationship: str | None
    traits: list[TraitView]
    threads: list[ThreadView]
    entities: list[EntityView]
    time_period: TimePeriodView
    contributor_display_name: str = ""
    gender: str | None = None
    archetype_answers: list[dict[str, Any]] = field(default_factory=list)
    is_first_summary: bool = False
