"""Long-running worker for the P2/P3/P5 producer queues."""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass

import structlog

from flashback.llm.errors import LLMError, LLMTimeout

from .runner import RunResult, run_once
from .sqs_client import ProducerSQSClient, ReceivedProducerMessage

log = structlog.get_logger("flashback.workers.producers")


@dataclass
class ProducerWorker:
    db_pool: object
    sqs: ProducerSQSClient
    embedding_sender: object
    settings: object
    allowed_producers: frozenset[str]
    embedding_model: str
    embedding_model_version: str
    sqs_wait_seconds: int = 20

    def run_forever(self, stop: "_StopSignal | None" = None) -> None:
        stop = stop or _StopSignal()
        stop.install()
        log.info(
            "producer.worker_started",
            allowed_producers=sorted(self.allowed_producers),
        )
        while not stop.requested:
            messages = self.sqs.receive(wait_seconds=self.sqs_wait_seconds)
            if not messages:
                continue
            for msg in messages:
                self.process_message(msg)
        log.info("producer.worker_stopped")

    def process_message(self, msg: ReceivedProducerMessage) -> RunResult | None:
        producer = msg.payload.producer
        person_id = msg.payload.person_id
        try:
            if producer not in self.allowed_producers:
                raise ValueError(
                    f"producer {producer!r} not allowed on this queue"
                )
            result = asyncio.run(
                run_once(
                    db_pool=self.db_pool,
                    embedding_sender=self.embedding_sender,
                    settings=self.settings,
                    producer_tag=producer,
                    person_id=person_id,
                    idempotency_key=msg.message_id,
                    embedding_model=self.embedding_model,
                    embedding_model_version=self.embedding_model_version,
                )
            )
        except LLMTimeout as exc:
            log.warning(
                "producer.llm_timeout_no_ack",
                message_id=msg.message_id,
                person_id=str(person_id),
                producer=producer,
                error=str(exc),
            )
            return None
        except LLMError as exc:
            log.error(
                "producer.llm_error_acking",
                message_id=msg.message_id,
                person_id=str(person_id),
                producer=producer,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.sqs.delete(msg.receipt_handle)
            return None
        except Exception as exc:  # noqa: BLE001
            log.error(
                "producer.unexpected_error",
                message_id=msg.message_id,
                person_id=str(person_id),
                producer=producer,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None

        log.info(
            "producer.message_complete",
            message_id=msg.message_id,
            person_id=str(person_id),
            producer=producer,
            **result.summary(),
        )
        self.sqs.delete(msg.receipt_handle)
        return result


class _StopSignal:
    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        try:
            signal.signal(signal.SIGTERM, self._handle)
        except (AttributeError, ValueError):
            pass

    def _handle(self, *_args) -> None:
        self.requested = True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

