"""Profile Summary Generator drain loop.

Sibling to :mod:`flashback.workers.trait_synthesizer.worker`. Long-
running process (CLI: ``python -m flashback.workers.profile_summary
run``). One SQS message at a time.

Per-message sequence:

  1. Pop one message; parse ``person_id`` from the body.
  2. ``runner.run_once`` with ``idempotency_key = msg.message_id``.
  3. On success → ack the message.

Failure-mode policy (matches step 13 — profile summaries are
enhancement, not critical):

* ``LLMTimeout`` — transient. Do NOT ack; SQS visibility timeout
  redrives.
* ``LLMError`` (covers ``LLMMalformedResponse`` and any other
  permanent LLM failure) — fail-soft. Log and ACK. We'd rather drop
  one summary than block the queue.
* Generic ``Exception`` — do NOT ack. Programmer error or a DB issue;
  SQS will redrive after the visibility timeout.

NB on the exception order: ``LLMTimeout`` and ``LLMMalformedResponse``
are both subclasses of ``LLMError``. We catch ``LLMTimeout`` first
(no ack) and ``LLMError`` afterwards (ack).
"""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass

import structlog

from flashback.http.logging import configure_logging
from flashback.llm.errors import LLMError, LLMTimeout
from flashback.profile_facts.extraction import FactExtractionConfig

from .runner import RunResult, run_once
from .sqs_client import (
    ProfileSummarySQSClient,
    ReceivedProfileSummaryMessage,
)
from .summary_llm import SummaryLLMConfig

log = structlog.get_logger("flashback.workers.profile_summary")


@dataclass
class ProfileSummaryWorker:
    """Long-running worker. Construct with stubs in tests; call
    :meth:`process_message` directly.

    ``fact_extraction_cfg`` and ``embedding_sender`` are optional so
    legacy callers and the run-once CLI can still construct a
    summary-only worker. When both are present, each message also runs
    profile-fact extraction (best-effort).
    """

    db_pool: object
    sqs: ProfileSummarySQSClient
    summary_cfg: SummaryLLMConfig
    settings: object
    top_traits_max: int
    top_threads_max: int
    top_entities_max: int
    sqs_wait_seconds: int = 20
    fact_extraction_cfg: FactExtractionConfig | None = None
    embedding_sender: object = None
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    transient_failure_backoff_seconds: float = 5.0

    def run_forever(self, stop: "_StopSignal | None" = None) -> None:
        stop = stop or _StopSignal()
        stop.install()
        log.info("profile_summary.worker_started")
        while not stop.requested:
            messages = self.sqs.receive(wait_seconds=self.sqs_wait_seconds)
            if not messages:
                continue
            for msg in messages:
                self.process_message(msg)
        log.info("profile_summary.worker_stopped")

    # ------------------------------------------------------------------
    # Per-message work
    # ------------------------------------------------------------------

    def process_message(
        self, msg: ReceivedProfileSummaryMessage
    ) -> RunResult | None:
        """Drain one message. Returns the run result for tests."""
        person_id = str(msg.payload.person_id)
        idempotency_key = msg.payload.idempotency_key or msg.message_id
        try:
            result = run_once(
                db_pool=self.db_pool,
                summary_cfg=self.summary_cfg,
                settings=self.settings,
                person_id=person_id,
                idempotency_key=idempotency_key,
                top_traits_max=self.top_traits_max,
                top_threads_max=self.top_threads_max,
                top_entities_max=self.top_entities_max,
                fact_extraction_cfg=self.fact_extraction_cfg,
                embedding_sender=self.embedding_sender,
                embedding_model=self.embedding_model,
                embedding_model_version=self.embedding_model_version,
            )
        except LLMTimeout as exc:
            log.warning(
                "profile_summary.llm_timeout_no_ack",
                message_id=msg.message_id,
                person_id=person_id,
                error=str(exc),
            )
            time.sleep(self.transient_failure_backoff_seconds)
            return None  # don't ack; SQS redrives
        except LLMError as exc:
            # Permanent for this run — fail-soft and ack so we don't
            # loop on a malformed response. Subsumes LLMMalformedResponse.
            log.error(
                "profile_summary.llm_permanent_error_acking",
                message_id=msg.message_id,
                person_id=person_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.sqs.delete(msg.receipt_handle)
            return None
        except Exception as exc:  # noqa: BLE001
            log.error(
                "profile_summary.unexpected_error",
                message_id=msg.message_id,
                person_id=person_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None  # don't ack; SQS redrives

        log.info(
            "profile_summary.message_complete",
            message_id=msg.message_id,
            person_id=person_id,
            **result.summary(),
        )
        self.sqs.delete(msg.receipt_handle)
        return result


# ---------------------------------------------------------------------------
# Stop-signal handler (mirrors the sibling workers)
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
