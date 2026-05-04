"""Pydantic and dataclass models for the Thread Detector worker.

Three surfaces:

* :class:`ThreadDetectorMessage` — the queue payload pushed by the
  Extraction Worker's ``thread_trigger`` and drained here. The exact
  values that satisfied the trigger are passed for diagnostics; the
  worker re-validates the trigger condition itself before doing work.

* :class:`ClusterableMoment` — a slim moment row pulled from
  ``active_moments`` for clustering. Only includes the columns the
  worker uses (id, narrative, title, embedding).

* :class:`Cluster` — the dataclass output of HDBSCAN. Holds the member
  moment ids, the matrix of embeddings, the centroid, and the cluster
  confidence (mean per-point membership probability).

LLM result models (:class:`NamingResult`, :class:`P4Question`,
:class:`P4Result`) live here too so the LLM wrappers can return typed
shapes the persistence layer can rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Inbound queue payload
# ---------------------------------------------------------------------------


class ThreadDetectorMessage(BaseModel):
    """Body of one ``thread_detector`` SQS message."""

    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    active_count_at_trigger: int
    last_count_at_trigger: int


# ---------------------------------------------------------------------------
# Clusterable moment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterableMoment:
    """One active moment with a non-NULL narrative embedding."""

    id: str
    title: str
    narrative: str
    embedding: list[float]


# ---------------------------------------------------------------------------
# Cluster output
# ---------------------------------------------------------------------------


@dataclass
class Cluster:
    """One HDBSCAN-detected cluster of moments.

    ``centroid`` is the L2-normalized mean of the member embeddings, ready
    for cosine-distance lookup against ``threads.description_embedding``
    (which pgvector stores normalized for the ``vector_cosine_ops`` index).

    ``confidence`` is the mean per-point membership probability returned
    by HDBSCAN's ``probabilities_`` array, clipped to [0, 1]. We persist
    it as ``threads.confidence`` so reviewers can see how tight the
    cluster was.
    """

    member_moment_ids: list[str]
    member_embeddings: np.ndarray  # shape (n, d)
    centroid: np.ndarray            # shape (d,), L2-normalized
    confidence: float


# ---------------------------------------------------------------------------
# LLM result shapes
# ---------------------------------------------------------------------------


class NamingResult(BaseModel):
    """Parsed ``name_thread`` tool arguments."""

    model_config = ConfigDict(extra="forbid")

    coherent: bool
    reasoning: str
    name: str | None = None
    description: str | None = None
    generation_prompt: str | None = None


class P4Question(BaseModel):
    """One ``thread_deepen`` question proposal."""

    model_config = ConfigDict(extra="forbid")

    text: str
    themes: list[str] = Field(min_length=1)


class P4Result(BaseModel):
    """Parsed ``propose_thread_deepen_questions`` tool arguments."""

    model_config = ConfigDict(extra="forbid")

    questions: list[P4Question] = Field(min_length=1, max_length=2)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Match-or-create result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreadMatchResult:
    """Outcome of looking up an existing thread by centroid similarity.

    ``existing_thread_id`` is set when the closest active thread is within
    the configured cosine distance threshold. Otherwise both fields are
    ``None`` (no candidates) or only ``existing_thread_distance`` is set
    (a candidate exists but is too far — caller creates a new thread).
    """

    existing_thread_id: str | None
    existing_thread_distance: float | None

    @property
    def is_match(self) -> bool:
        return self.existing_thread_id is not None


# ---------------------------------------------------------------------------
# Existing-thread snapshot (for P4 input on link path)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreadSnapshot:
    """Minimal projection of a thread row used to seed the P4 prompt."""

    id: str
    name: str
    description: str

    @classmethod
    def from_naming(cls, *, thread_id: str, naming: NamingResult) -> "ThreadSnapshot":
        """Build a snapshot from a freshly-named cluster."""
        return cls(
            id=thread_id,
            name=naming.name or "",
            description=naming.description or "",
        )


def _to_jsonable(value: Any) -> Any:  # pragma: no cover - utility kept tiny
    """Used only for queue payload debug dumps."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value
