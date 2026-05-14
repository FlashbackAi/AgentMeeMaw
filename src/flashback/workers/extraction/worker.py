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

import signal
import threading
import time
from dataclasses import dataclass

import structlog
from pydantic import ValidationError

from flashback.db.edges import EdgeValidationError
from flashback.http.logging import configure_logging
from flashback.llm.errors import LLMError, LLMTimeout

from .compatibility_llm import (
    CompatibilityLLMConfig,
    judge_compatibility,
)
from .extraction_llm import (
    EXTRACTION_PROMPT_VERSION,
    ExtractionLLMConfig,
    run_extraction,
)
from .idempotency import is_processed, mark_processed
from .persistence import (
    LLMProvenance,
    MomentDecision,
    TraitMergeResolution,
    fetch_person,
    find_existing_active_traits_by_name,
    persist_extraction,
)
from .outbox import (
    drain_extraction_outbox,
    enqueue_extraction_fanout,
    enqueue_thread_detector_trigger_if_due,
)
from .refinement import collect_entity_names_for_moment, find_refinement_candidates
from .schema import ExtractionMessage, ExtractionResult, drop_orphan_traits
from .trait_merge_llm import TraitMergeLLMConfig, merge_trait_description
from .sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
    ExtractionSQSClient,
    ReceivedMessage,
)
from .voyage_query import SyncVoyageQueryEmbedder
from .coverage import run_coverage_tracker
from .handover import run_handover_check
from flashback.entity_mention.cache_sync import invalidate_entity_name_cache
from flashback.workers.thread_detector.sqs_client import ThreadDetectorJobSender
from uuid import UUID as _UUID

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
    trait_merge_cfg: TraitMergeLLMConfig
    settings: object
    embedding_model: str
    embedding_model_version: str
    redis_client: object | None = None
    refinement_distance_threshold: float = 0.35
    refinement_candidate_limit: int = 3
    sqs_wait_seconds: int = 20
    visibility_timeout_seconds: int = 120
    visibility_heartbeat_interval_seconds: int = 45
    transient_failure_backoff_seconds: float = 5.0
    outbox_drain_limit: int = 100
    thread_detector_cadence: int = 15

    def run_forever(self, stop: "_StopSignal | None" = None) -> None:
        stop = stop or _StopSignal()
        stop.install()
        log.info("extraction.worker_started")
        while not stop.requested:
            self._drain_outbox()
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
            self._drain_outbox(source_sqs_message_id=msg.message_id)
            self.sqs.delete(msg.receipt_handle)
            return

        try:
            with _VisibilityExtender(
                self.sqs,
                msg.receipt_handle,
                timeout_seconds=self.visibility_timeout_seconds,
                interval_seconds=self.visibility_heartbeat_interval_seconds,
            ):
                self._extract_and_persist(
                    payload=msg.payload, message_id=msg.message_id
                )
        except LLMTimeout as exc:
            log.warning(
                "extraction.transient_llm_timeout_no_ack",
                message_id=msg.message_id,
                error=str(exc),
            )
            time.sleep(self.transient_failure_backoff_seconds)
            return
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
        self._drain_outbox(source_sqs_message_id=msg.message_id)

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
    ) -> None:
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
            contributor_display_name=payload.contributor_display_name or "",
        )

        # 2b. Invariant #18: drop orphan traits with no exemplifying moment
        # in this segment. The prompt instructs the LLM to skip them; this
        # is the backstop.
        extraction, orphan_traits_dropped = drop_orphan_traits(extraction)
        if orphan_traits_dropped:
            log.info(
                "extraction.orphan_traits_dropped",
                count=orphan_traits_dropped,
            )

        # 2c. Invariant #18 (cross-session merge): if any extracted trait
        # matches an active trait by case-insensitive name for this person,
        # route the new evidence into the existing row instead of inserting
        # a duplicate. The merge LLM blends the existing and new
        # descriptions; persistence then UPDATEs and re-embeds.
        extraction, trait_merge_resolutions = self._resolve_trait_merges(
            extraction=extraction,
            person_id=str(payload.person_id),
            subject_name=person.name,
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
                        llm_provenance=LLMProvenance(
                            provider=self.extraction_cfg.provider,
                            model=self.extraction_cfg.model,
                            prompt_version=EXTRACTION_PROMPT_VERSION,
                        ),
                        trait_merge_resolutions=trait_merge_resolutions,
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
                    outbox_jobs = enqueue_extraction_fanout(
                        cur,
                        source_sqs_message_id=message_id,
                        person_id=str(payload.person_id),
                        extraction=extraction,
                        persistence_result=persistence_result,
                        embedding_model=self.embedding_model,
                        embedding_model_version=self.embedding_model_version,
                    )
                    trigger_status = enqueue_thread_detector_trigger_if_due(
                        cur,
                        source_sqs_message_id=message_id,
                        person_id=str(payload.person_id),
                        cadence=self.thread_detector_cadence,
                        contributor_display_name=(
                            payload.contributor_display_name or ""
                        ),
                    )
                    if trigger_status.would_trigger:
                        outbox_jobs += 1

        if persistence_result.entity_ids and self.redis_client is not None:
            try:
                invalidate_entity_name_cache(
                    self.redis_client, _UUID(str(payload.person_id))
                )
            except Exception as exc:  # noqa: BLE001 - cache hygiene is best-effort
                log.warning(
                    "extraction.entity_name_cache_invalidation_failed",
                    person_id=str(payload.person_id),
                    error=str(exc),
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
            merge_suggestions=len(persistence_result.merge_suggestion_ids),
            subject_guard_dropped=persistence_result.dropped_entities_count,
            outbox_jobs=outbox_jobs,
        )

    def _resolve_trait_merges(
        self,
        *,
        extraction: ExtractionResult,
        person_id: str,
        subject_name: str,
    ) -> tuple[ExtractionResult, list[TraitMergeResolution | None]]:
        """Detect cross-session trait dedup matches and merge descriptions.

        For each extracted trait whose case-insensitive name already exists
        as an active trait for this person, call the small merge LLM to
        produce a cohesive description from the existing row and the new
        extraction. Returns a new :class:`ExtractionResult` whose matched
        traits carry the merged description, plus a per-index list of
        :class:`TraitMergeResolution` (``None`` for unmatched indexes).
        Runs outside the transaction so the slow LLM call doesn't hold
        locks.
        """
        if not extraction.traits:
            return extraction, []

        names = [t.name for t in extraction.traits]
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                existing_by_name = find_existing_active_traits_by_name(
                    cur, person_id=person_id, names=names
                )

        if not existing_by_name:
            return extraction, [None] * len(extraction.traits)

        resolutions: list[TraitMergeResolution | None] = []
        new_traits = list(extraction.traits)
        merges_run = 0
        for i, trait in enumerate(extraction.traits):
            match = existing_by_name.get(trait.name.strip().lower())
            if match is None:
                resolutions.append(None)
                continue
            existing_desc = match.description or ""
            new_desc = trait.description or ""
            if not new_desc:
                # Nothing new to merge — keep the existing description and
                # just route edges to the existing row.
                resolutions.append(
                    TraitMergeResolution(existing_trait_id=match.id)
                )
                new_traits[i] = trait.model_copy(
                    update={"description": existing_desc or None}
                )
                continue
            if not existing_desc:
                # Existing row has no description yet — adopt the new one
                # verbatim without an LLM call.
                merged_desc = new_desc
            else:
                merged_desc = merge_trait_description(
                    cfg=self.trait_merge_cfg,
                    settings=self.settings,
                    subject_name=subject_name,
                    trait_name=trait.name,
                    existing_description=existing_desc,
                    new_description=new_desc,
                )
                merges_run += 1
            new_traits[i] = trait.model_copy(
                update={"description": merged_desc}
            )
            resolutions.append(
                TraitMergeResolution(existing_trait_id=match.id)
            )

        if merges_run or any(r is not None for r in resolutions):
            log.info(
                "extraction.trait_merge_resolved",
                merges_run=merges_run,
                matched=sum(1 for r in resolutions if r is not None),
                total=len(extraction.traits),
            )
        return (
            extraction.model_copy(update={"traits": new_traits}),
            resolutions,
        )

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

    def _drain_outbox(self, *, source_sqs_message_id: str | None = None) -> int:
        return drain_extraction_outbox(
            self.db_pool,
            embedding_sender=self.embedding_sender,
            artifact_sender=self.artifact_sender,
            thread_detector_sender=self.thread_detector_sender,
            source_sqs_message_id=source_sqs_message_id,
            limit=self.outbox_drain_limit,
        )


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


class _VisibilityExtender:
    """Best-effort SQS visibility heartbeat for long LLM work."""

    def __init__(
        self,
        sqs: object,
        receipt_handle: str,
        *,
        timeout_seconds: int,
        interval_seconds: int,
    ) -> None:
        self._sqs = sqs
        self._receipt_handle = receipt_handle
        self._timeout_seconds = timeout_seconds
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if not hasattr(self._sqs, "change_visibility"):
            return self
        self._thread = threading.Thread(
            target=self._run,
            name="extraction-sqs-visibility-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._sqs.change_visibility(
                    self._receipt_handle,
                    timeout_seconds=self._timeout_seconds,
                )
                log.info(
                    "extraction.visibility_extended",
                    timeout_seconds=self._timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "extraction.visibility_extend_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )


def _configure_logging() -> None:
    configure_logging()
