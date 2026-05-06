"""
SQS clients for the Extraction Worker.

Sibling to ``flashback.workers.embedding.sqs_client``: sync, ``boto3``-based,
no async. Three concerns live here:

* :class:`ExtractionSQSClient` — receive and ack messages on the
  ``extraction`` queue.
* :class:`EmbeddingJobSender` — push embedding jobs onto the ``embedding``
  queue. Body shape matches the existing
  :class:`flashback.workers.embedding.sqs_client.SQSClient.send_embedding_job`
  exactly so the embedding worker drains it without a schema bump.
* :class:`ArtifactJobSender` — push artifact-generation jobs onto the
  ``artifact_generation`` queue. Node owns the consumer for that queue;
  this side only writes the producer surface.

The queue payload for ``extraction`` (inbound) is the one
``flashback.queues.extraction.ExtractionQueueProducer`` writes. We do not
re-derive the payload shape — :class:`flashback.workers.extraction.schema.ExtractionMessage`
parses it once and the rest of the worker uses the typed model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from flashback.queues.boto import make_sqs_client

from .schema import ExtractionMessage

log = structlog.get_logger("flashback.workers.extraction.sqs_client")


@dataclass(frozen=True)
class ReceivedMessage:
    """One inbound extraction message plus SQS bookkeeping."""

    message_id: str
    receipt_handle: str
    payload: ExtractionMessage
    raw_body: str


@dataclass
class ExtractionSQSClient:
    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def receive(self, *, wait_seconds: int = 20) -> list[ReceivedMessage]:
        """Long-poll for a single extraction message (one at a time)."""
        resp = self._get_client().receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
        )
        out: list[ReceivedMessage] = []
        for msg in resp.get("Messages", []) or []:
            body = msg["Body"]
            try:
                payload = ExtractionMessage.model_validate_json(body)
            except ValidationError as exc:
                log.error(
                    "extraction.malformed_message_acking",
                    message_id=msg.get("MessageId"),
                    error=str(exc),
                )
                self.delete(msg["ReceiptHandle"])
                continue
            out.append(
                ReceivedMessage(
                    message_id=msg["MessageId"],
                    receipt_handle=msg["ReceiptHandle"],
                    payload=payload,
                    raw_body=body,
                )
            )
        return out

    def delete(self, receipt_handle: str) -> None:
        self._get_client().delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )

    def change_visibility(self, receipt_handle: str, *, timeout_seconds: int) -> None:
        self._get_client().change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=timeout_seconds,
        )


@dataclass
class EmbeddingJobSender:
    """
    Producer for the ``embedding`` queue.

    Body shape matches
    :class:`flashback.workers.embedding.sqs_client.SQSClient.send_embedding_job`
    one-for-one. Keep them aligned: the embedding worker is the consumer.
    """

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def send(
        self,
        *,
        record_type: str,
        record_id: str,
        source_text: str,
        embedding_model: str,
        embedding_model_version: str,
    ) -> str:
        payload = {
            "record_type": record_type,
            "record_id": record_id,
            "source_text": source_text,
            "embedding_model": embedding_model,
            "embedding_model_version": embedding_model_version,
        }
        resp = self._get_client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
        )
        return str(resp["MessageId"])


@dataclass
class ArtifactJobSender:
    """
    Producer for the ``artifact_generation`` queue.

    Per ARCHITECTURE.md §12, Node consumes this queue, calls the
    image/video model, uploads to S3, and writes the URL columns back to
    Postgres. The agent side never touches the URL columns.

    Body shape:

        {
            "record_type":      "person" | "moment" | "thread" | "entity",
            "record_id":        "<uuid>",
            "person_id":        "<uuid>",
            "artifact_kind":    "image" | "video",
            "generation_prompt": "<one-sentence visual description>"
        }
    """

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def send(
        self,
        *,
        record_type: str,
        record_id: str,
        person_id: str,
        artifact_kind: str,
        generation_prompt: str,
    ) -> str:
        payload = {
            "record_type": record_type,
            "record_id": record_id,
            "person_id": person_id,
            "artifact_kind": artifact_kind,
            "generation_prompt": generation_prompt,
        }
        resp = self._get_client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
        )
        return str(resp["MessageId"])
