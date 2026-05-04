"""Trait Synthesizer worker drain loop.

Sibling to :mod:`flashback.workers.thread_detector.worker`. Long-running
process (CLI: ``python -m flashback.workers.trait_synthesizer run``).
One SQS message at a time.

Per-message sequence:

  1. Pop one message; parse ``person_id`` from the body.
  2. ``runner.run_once`` with ``idempotency_key = msg.message_id``.
  3. On success → ack the message.

Failure-mode policy (CLAUDE.md §4 invariant — but ARCHITECTURE.md §3.14
explicitly: trait synthesis is enhancement, not critical):

* ``LLMTimeout`` — transient. Do NOT ack; SQS visibility timeout
  redrives. The next session's wrap will re-trigger us anyway, and a
  Voyage / OpenAI hiccup shouldn't strand the message indefinitely
  if it's actually a permanent issue (the redrive policy / DLQ
  catches that at the queue level).
* ``LLMError`` (covers ``LLMMalformedResponse`` and any other
  permanent LLM failure) — fail-soft. Log and ACK. Trait synthesis
  is optional; we'd rather drop one synth than block the queue.
* Generic ``Exception`` — do NOT ack. Programmer error or a DB issue;
  SQS will redrive after the visibility timeout, and the next attempt
  will see the same idempotency state (i.e., not processed yet).

NB on the exception order: ``LLMTimeout`` and ``LLMMalformedResponse``
are both subclasses of ``LLMError``. We catch ``LLMTimeout`` first
(no ack) and ``LLMError`` afterwards (ack — covers
``LLMMalformedResponse`` and the base class).
"""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass

import structlog

from flashback.llm.errors import LLMError, LLMTimeout
from flashback.workers.extraction.sqs_client import EmbeddingJobSender

from .runner import RunResult, run_once
from .sqs_client import (
    ReceivedTraitSynthMessage,
    TraitSynthesizerSQSClient,
)
from .synth_llm import SynthLLMConfig

log = structlog.get_logger("flashback.workers.trait_synthesizer")


@dataclass
class TraitSynthesizerWorker:
    """Long-running worker. Construct with stubs in tests; call
    :meth:`process_message` directly."""

    db_pool: object
    sqs: TraitSynthesizerSQSClient
    embedding_sender: EmbeddingJobSender
    synth_cfg: SynthLLMConfig
    settings: object
    embedding_model: str
    embedding_model_version: str
    sqs_wait_seconds: int = 20

    def run_forever(self, stop: "_StopSignal | None" = None) -> None:
        stop = stop or _StopSignal()
        stop.install()
        log.info("trait_synthesizer.worker_started")
        while not stop.requested:
            messages = self.sqs.receive(wait_seconds=self.sqs_wait_seconds)
            if not messages:
                continue
            for msg in messages:
                self.process_message(msg)
        log.info("trait_synthesizer.worker_stopped")

    # ------------------------------------------------------------------
    # Per-message work
    # ------------------------------------------------------------------

    def process_message(self, msg: ReceivedTraitSynthMessage) -> RunResult | None:
        """Drain one message. Returns the run result for tests."""
        person_id = str(msg.payload.person_id)
        try:
            result = run_once(
                db_pool=self.db_pool,
                embedding_sender=self.embedding_sender,
                synth_cfg=self.synth_cfg,
                settings=self.settings,
                person_id=person_id,
                idempotency_key=msg.message_id,
                embedding_model=self.embedding_model,
                embedding_model_version=self.embedding_model_version,
            )
        except LLMTimeout as exc:
            log.warning(
                "trait_synthesizer.llm_timeout_no_ack",
                message_id=msg.message_id,
                person_id=person_id,
                error=str(exc),
            )
            return None  # don't ack; SQS redrives
        except LLMError as exc:
            # Permanent for this run — fail-soft and ack so we don't
            # loop on a malformed response. Subsumes LLMMalformedResponse.
            log.error(
                "trait_synthesizer.llm_permanent_error_acking",
                message_id=msg.message_id,
                person_id=person_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.sqs.delete(msg.receipt_handle)
            return None
        except Exception as exc:  # noqa: BLE001
            log.error(
                "trait_synthesizer.unexpected_error",
                message_id=msg.message_id,
                person_id=person_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None  # don't ack; SQS redrives

        log.info(
            "trait_synthesizer.message_complete",
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
