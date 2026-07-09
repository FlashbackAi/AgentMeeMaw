"""
Extraction Worker (step 11) for the Flashback Legacy Mode agent service.

The worker is a long-running process that drains the ``extraction`` SQS
queue. For each closed segment it:

* Calls a Sonnet-class LLM with a structured tool to extract moments,
  entities, traits, and dropped-reference questions.
* Runs a vector + entity-overlap search for refinement candidates,
  consulting a small (gpt-5.1) LLM for each candidate to decide
  ``refinement | contradiction | independent``.
* Writes everything in a single Postgres transaction. Edge writes go
  through ``flashback.db.edges.validate_edge`` per CLAUDE.md invariant.
* Updates ``persons.coverage_state`` (Coverage Tracker) and conditionally
  flips ``persons.phase`` to ``steady`` (Handover Check) inside the same
  transaction.
* Inside the same transaction, inserts embedding jobs (one per embedded
  row), artifact jobs (one per artifact-bearing row), and — when the
  every-15-new-moments gate trips — a Thread Detector trigger into the
  ``extraction_outbox``; the post-commit drain sends them to SQS.

Process model mirrors ``flashback.workers.embedding``: sync ``boto3``,
sync ``psycopg``, no async on the loop. The two LLM calls are async-only,
so the worker runs them through ``asyncio.run`` per call. One message is
processed at a time — no batching across segments.

Reference: ARCHITECTURE.md §3.9–§3.13, §6, §7, §8, §9.
"""

from __future__ import annotations

from .schema import (
    DroppedReference,
    ExtractedEntity,
    ExtractedMoment,
    ExtractedTrait,
    ExtractionResult,
    TimeAnchor,
)

__all__ = [
    "DroppedReference",
    "ExtractedEntity",
    "ExtractedMoment",
    "ExtractedTrait",
    "ExtractionResult",
    "TimeAnchor",
]
