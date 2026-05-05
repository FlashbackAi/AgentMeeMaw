"""
Extraction Worker drain loop.

Sibling to ``flashback.workers.embedding.worker``. Differences:

* Process one message at a time (no batching across segments).
* Two LLM calls per message — extraction (Sonnet) and 0..N
  compatibility checks (gpt-5.1). Costs scale with refinement
  candidates returned from the vector search.
* Single Postgres transaction per segment covers persistence,
  Coverage Tracker, Handover Check, and the idempotency row.
* Embedding + artifact queue pushes happen post-commit; Thread
  Detector trigger logging happens post-commit.

Invariants honoured (CLAUDE.md §4):

* #1 (status='active'): refinement search uses ``active_moments``;
  Coverage Tracker / Handover Check key off persons.
* #2 (person_id scoping): all writes carry the legacy person_id;
  refinement query is scoped.
* #3 (no cross-model vectors): embedding pushes carry the configured
  ``EMBEDDING_MODEL`` / ``EMBEDDING_MODEL_VERSION``; the embedding
  worker enforces the version guard on UPDATE.
* #4 (no inline embeddings): stored vectors are never written by this
  worker. Only the Voyage *query* embedding is run inline, and that
  vector never lands in a column.
* #5 (supersession repoints all edges atomically): handled inside the
  persistence transaction.
* #6 (under-extract): the system prompt instructs the LLM accordingly.
* #15 (rolling-summary ownership): the segment detector regenerates
  the summary on boundary; this worker treats it as input only.
"""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass

import structlog
from pydantic import ValidationError

from flashback.db.edges import EdgeValidationError
from flashback.llm.errors import LLMError

from .compatibility_llm import (
    CompatibilityLLMConfig,
    judge_compatibility,
)
from .extraction_llm import ExtractionLLMConfig, run_extraction
from .idempotency import is_processed, mark_processed
from .persistence import (
    MomentDecision,
    PersistenceResult,
    fetch_person,
    persist_extraction,
)
from .post_commit import push_artifact_jobs, push_embedding_jobs
from .refinement import collect_entity_names_for_moment, find_refinement_candidates
from .schema import ExtractionMessage, ExtractionResult
from .sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
    ExtractionSQSClient,
    ReceivedMessage,
)
from .thread_trigger import check_and_push_thread_detector_trigger
from .voyage_query import SyncVoyageQueryEmbedder
from .coverage import run_coverage_tracker
from .handover import run_handover_check
from flashback.workers.thread_detector.sqs_client import ThreadDetectorJobSender

log = structlog.get_logger("flashback.workers.extraction")


# ---------------------------------------------------------------------------
# Wired worker (dependency container)
# ---------------------------------------------------------------------------


@dataclass
class ExtractionWorker:
    """
    Long-running worker. Hold all dependencies; ``run_forever`` drives
    the loop. Tests construct one with stub clients and call
    :meth:`process_message` directly.
    """

    db_pool: object
    sqs: ExtractionSQSClient
    embedding_sender: EmbeddingJobSender
    artifact_sender: ArtifactJobSender
    thread_detector_sender: ThreadDetectorJobSender
    voyage: SyncVoyageQueryEmbedder
    extraction_cfg: ExtractionLLMConfig
    compatibility_cfg: CompatibilityLLMConfig
    settings: object
    embedding_model: str
    embedding_model_version: str
    refinement_distance_threshold: float = 0.35
    refinement_candidate_limit: int = 3
    sqs_wait_seconds: int = 20

    def run_forever(self, stop: "_StopSignal | None" = None) -> None:
        stop = stop or _StopSignal()
        stop.install()
        log.info("extraction.worker_started")
        while not stop.requested:
            messages = self.sqs.receive(wait_seconds=self.sqs_wait_seconds)
            if not messages:
                continue
            for msg in messages:
                self.process_message(msg)
        log.info("extraction.worker_stopped")

    # ------------------------------------------------------------------
    # Per-message work
    # ------------------------------------------------------------------

    def process_message(self, msg: ReceivedMessage) -> None:
        """
        Idempotent processing of one extraction message.

        Side-effects on success:

          * Postgres rows for persons coverage, moments, entities, traits,
            edges, dropped-reference questions, processed_extractions row.
          * Embedding + artifact SQS sends post-commit.
          * SQS message acked.

        On any failure: nothing acked, nothing pushed. SQS visibility
        timeout will redrive.
        """
        if self._already_processed(msg.message_id):
            log.info(
                "extraction.skipped_already_processed",
                message_id=msg.message_id,
            )
            self.sqs.delete(msg.receipt_handle)
            return

        try:
            persistence_result, extraction = self._extract_and_persist(
                payload=msg.payload, message_id=msg.message_id
            )
        except (LLMError, ValidationError, EdgeValidationError, Exception) as exc:  # noqa: BLE001
            log.error(
                "extraction.failed",
                message_id=msg.message_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return  # don't ack; SQS redrives

        # Post-commit fan-out — failures here are recoverable separately;
        # we still ack the message because the graph state is correct.
        try:
            push_embedding_jobs(
                sender=self.embedding_sender,
                extraction=extraction,
                moment_ids=persistence_result.moment_ids,
                surviving_entities=persistence_result.surviving_entities,
                entity_ids=persistence_result.entity_ids,
                trait_ids=persistence_result.trait_ids,
                question_ids=persistence_result.question_ids,
                embedding_model=self.embedding_model,
                embedding_model_version=self.embedding_model_version,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "extraction.embedding_push_failed",
                message_id=msg.message_id,
                error=str(exc),
            )

        try:
            push_artifact_jobs(
                sender=self.artifact_sender,
                person_id=str(msg.payload.person_id),
                moments=extraction.moments,
                moment_ids=persistence_result.moment_ids,
                surviving_entities=persistence_result.surviving_entities,
                entity_ids=persistence_result.entity_ids,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "extraction.artifact_push_failed",
                message_id=msg.message_id,
                error=str(exc),
            )

        try:
            check_and_push_thread_detector_trigger(
                self.db_pool,
                person_id=str(msg.payload.person_id),
                sender=self.thread_detector_sender,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "extraction.thread_trigger_push_failed",
                error=str(exc),
            )

        self.sqs.delete(msg.receipt_handle)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _already_processed(self, message_id: str) -> bool:
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                return is_processed(cur, message_id)

    def _extract_and_persist(
        self, *, payload: ExtractionMessage, message_id: str
    ) -> tuple[PersistenceResult, ExtractionResult]:
        """
        The heavy path. Returns the persistence result and the raw
        extraction result; the caller drives post-commit pushes off both.
        """
        # 1. Subject + segment context outside the transaction (read-only).
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                person = fetch_person(cur, str(payload.person_id))

        # 2. Extraction LLM call (slow; outside the transaction).
        extraction = run_extraction(
            cfg=self.extraction_cfg,
            settings=self.settings,
            subject_name=person.name,
            subject_relationship=None,
            prior_rolling_summary=payload.prior_rolling_summary,
            segment_turns=payload.segment_turns,
        )

        # 3. Refinement detection (vector search + per-candidate compat call).
        decisions = self._build_moment_decisions(
            extraction=extraction, person_id=str(payload.person_id)
        )

        # 4. Single transaction.
        with self.db_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    persistence_result = persist_extraction(
                        cur,
                        person=person,
                        extraction=extraction,
                        moment_decisions=decisions,
                        seeded_question_id=(
                            str(payload.seeded_question_id)
                            if payload.seeded_question_id is not None
                            else None
                        ),
                    )
                    run_coverage_tracker(
                        cur,
                        person_id=str(payload.person_id),
                        moment_signals=persistence_result.moment_signals,
                    )
                    run_handover_check(cur, person_id=str(payload.person_id))
                    mark_processed(
                        cur,
                        message_id=message_id,
                        person_id=str(payload.person_id),
                        session_id=str(payload.session_id),
                        moments_written=len(persistence_result.moment_ids),
                    )

        log.info(
            "extraction.persisted",
            message_id=message_id,
            person_id=str(payload.person_id),
            moments_written=len(persistence_result.moment_ids),
            entities_written=len(persistence_result.entity_ids),
            traits_written=len(persistence_result.trait_ids),
            questions_written=len(persistence_result.question_ids),
            superseded=len(persistence_result.superseded_moment_ids),
            subject_guard_dropped=persistence_result.dropped_entities_count,
        )
        return persistence_result, extraction

    def _build_moment_decisions(
        self, *, extraction: ExtractionResult, person_id: str
    ) -> list[MomentDecision]:
        decisions: list[MomentDecision] = []
        for moment in extraction.moments:
            entity_names = collect_entity_names_for_moment(extraction, moment)
            candidates = find_refinement_candidates(
                new_moment=moment,
                new_moment_entity_names=entity_names,
                person_id=person_id,
                voyage=self.voyage,
                db_pool=self.db_pool,
                embedding_model=self.embedding_model,
                embedding_model_version=self.embedding_model_version,
                distance_threshold=self.refinement_distance_threshold,
                candidate_limit=self.refinement_candidate_limit,
            )
            decision = MomentDecision(moment=moment)
            for candidate in candidates:
                response = judge_compatibility(
                    cfg=self.compatibility_cfg,
                    settings=self.settings,
                    new_moment=moment,
                    candidate=candidate,
                )
                if response.verdict == "refinement":
                    decision.supersedes_id = candidate.id
                    break  # take the first refinement match
                if response.verdict == "contradiction":
                    decision.contradicts_ids.append(candidate.id)
                # independent — keep looking
            decisions.append(decision)
        return decisions


# ---------------------------------------------------------------------------
# Stop-signal handler (mirrors the embedding worker)
# ---------------------------------------------------------------------------


class _StopSignal:
    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        try:
            signal.signal(signal.SIGTERM, self._handle)
        except (AttributeError, ValueError):
            # SIGTERM unavailable on Windows main-thread signal API.
            pass

    def _handle(self, *_args) -> None:
        self.requested = True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
