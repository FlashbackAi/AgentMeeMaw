"""Thread Detector worker drain loop.

Sibling to :mod:`flashback.workers.extraction.worker`. Runs as its own
long-running process (CLI: ``python -m flashback.workers.thread_detector
run``). One SQS message at a time. Per-cluster transactions; a single
cluster's failure does not abort the run.

Control flow per message:

  1. Re-validate the trigger (per CLAUDE.md §4 invariant #14). Stale
     messages are acked-and-skipped without writes.
  2. Fetch active moments with non-NULL embeddings on the current model.
     Fewer than ``min_cluster_size`` → ack, update
     ``moments_at_last_thread_run``, return.
  3. HDBSCAN clusters the moment embeddings.
  4. For each cluster: ``process_cluster`` (match-or-create, naming
     LLM, evidences edges, P4 LLM, questions, post-commit pushes).
     Per-cluster failures are caught and logged; the run continues.
  5. If at least one cluster succeeded, update
     ``moments_at_last_thread_run`` to the current active count.
     Otherwise leave it alone so the trigger fires again on retry.
  6. Ack the SQS message.

Failures BEFORE step 6 do not ack → SQS visibility timeout will redrive.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass

import structlog

from flashback.http.logging import configure_logging

from .clustering import count_outliers, run_hdbscan
from .naming_llm import NamingLLMConfig
from .p4_llm import P4LLMConfig
from .persistence import (
    ClusterOutcome,
    fetch_clusterable_moments,
    fetch_person_name,
    process_cluster,
)
from .schema import Cluster, ClusterableMoment
from .sqs_client import (
    ReceivedThreadDetectorMessage,
    ThreadDetectorSQSClient,
)
from .trigger_check import trigger_state, update_moments_at_last_thread_run

log = structlog.get_logger("flashback.workers.thread_detector")


@dataclass
class ThreadDetectorWorker:
    """Long-running worker. Construct with stubs in tests; call
    :meth:`process_message` directly."""

    db_pool: object
    sqs: ThreadDetectorSQSClient
    embedding_sender: object  # has .send(record_type=, record_id=, source_text=, ...)
    artifact_sender: object   # has .send(record_type=, record_id=, person_id=, ...)
    naming_cfg: NamingLLMConfig
    p4_cfg: P4LLMConfig
    settings: object
    embedding_model: str
    embedding_model_version: str
    min_cluster_size: int = 3
    existing_match_distance: float = 0.4
    sqs_wait_seconds: int = 20
    thread_detector_cadence: int = 15

    def run_forever(self, stop: "_StopSignal | None" = None) -> None:
        stop = stop or _StopSignal()
        stop.install()
        log.info("thread_detector.worker_started")
        while not stop.requested:
            messages = self.sqs.receive(wait_seconds=self.sqs_wait_seconds)
            if not messages:
                continue
            for msg in messages:
                self.process_message(msg)
        log.info("thread_detector.worker_stopped")

    # ------------------------------------------------------------------
    # Per-message work
    # ------------------------------------------------------------------

    def process_message(
        self, msg: ReceivedThreadDetectorMessage
    ) -> list[ClusterOutcome]:
        """Drain one message. Returns the per-cluster outcomes for tests."""
        person_id = str(msg.payload.person_id)
        contributor_display_name = msg.payload.contributor_display_name or ""

        try:
            outcomes = self._run_for_person(
                person_id=person_id,
                contributor_display_name=contributor_display_name,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "thread_detector.run_failed",
                person_id=person_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return []  # don't ack; SQS redrives

        # Ack — work is durable in Postgres, not in the message.
        self.sqs.delete(msg.receipt_handle)
        return outcomes

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_for_person(
        self,
        *,
        person_id: str,
        contributor_display_name: str = "",
    ) -> list[ClusterOutcome]:
        # 1. Re-validate trigger (idempotency).
        state = trigger_state(
            self.db_pool,
            person_id=person_id,
            cadence=self.thread_detector_cadence,
        )
        if not state.valid:
            log.info(
                "thread_detector.trigger_stale",
                person_id=person_id,
                active_count=state.active_count,
                last_count=state.last_count,
                delta=state.delta,
            )
            return []

        # 2. Fetch clusterable moments.
        moments = fetch_clusterable_moments(
            self.db_pool,
            person_id=person_id,
            embedding_model=self.embedding_model,
            embedding_model_version=self.embedding_model_version,
        )
        log.info(
            "thread_detector.fetched_moments",
            person_id=person_id,
            count=len(moments),
        )
        if len(moments) < self.min_cluster_size:
            log.info(
                "thread_detector.not_enough_moments",
                count=len(moments),
                min_cluster_size=self.min_cluster_size,
            )
            update_moments_at_last_thread_run(
                self.db_pool, person_id=person_id
            )
            return []

        # 3. Cluster.
        clusters = run_hdbscan(
            moments, min_cluster_size=self.min_cluster_size
        )
        log.info(
            "thread_detector.clusters_found",
            person_id=person_id,
            count=len(clusters),
            outliers=count_outliers(moments, clusters),
        )

        if not clusters:
            update_moments_at_last_thread_run(
                self.db_pool, person_id=person_id
            )
            return []

        # 4. Per-cluster work.
        person_name = fetch_person_name(self.db_pool, person_id=person_id)
        moment_lookup = {m.id: m for m in moments}

        outcomes: list[ClusterOutcome] = []
        clusters_processed = 0
        for cluster in clusters:
            try:
                outcome = self._process_one_cluster(
                    cluster=cluster,
                    moment_lookup=moment_lookup,
                    person_id=person_id,
                    person_name=person_name,
                    contributor_display_name=contributor_display_name,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "thread_detector.cluster_failed",
                    person_id=person_id,
                    cluster_size=len(cluster.member_moment_ids),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue
            outcomes.append(outcome)
            # An "incoherent" cluster is intentionally skipped (no thread
            # written) but counts as processed: the worker has done its
            # job and there is no reason to retry it.
            clusters_processed += 1

        # 5. Update baseline if at least one cluster ran end-to-end.
        if clusters_processed > 0:
            update_moments_at_last_thread_run(
                self.db_pool, person_id=person_id
            )

        return outcomes

    def _process_one_cluster(
        self,
        *,
        cluster: Cluster,
        moment_lookup: dict[str, ClusterableMoment],
        person_id: str,
        person_name: str,
        contributor_display_name: str = "",
    ) -> ClusterOutcome:
        member_moments = [
            moment_lookup[mid] for mid in cluster.member_moment_ids
        ]
        return process_cluster(
            db_pool=self.db_pool,
            cluster=cluster,
            member_moments=member_moments,
            person_id=person_id,
            person_name=person_name,
            naming_cfg=self.naming_cfg,
            p4_cfg=self.p4_cfg,
            settings=self.settings,
            embedding_model=self.embedding_model,
            embedding_model_version=self.embedding_model_version,
            distance_threshold=self.existing_match_distance,
            embedding_job_pusher=self._push_embedding_job,
            artifact_job_pusher=self._push_artifact_job,
            contributor_display_name=contributor_display_name,
        )

    def _push_embedding_job(self, **kwargs) -> None:
        self.embedding_sender.send(**kwargs)  # type: ignore[attr-defined]

    def _push_artifact_job(self, **kwargs) -> None:
        self.artifact_sender.send(**kwargs)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Stop-signal handler (mirrors the extraction / embedding workers)
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
    configure_logging()
