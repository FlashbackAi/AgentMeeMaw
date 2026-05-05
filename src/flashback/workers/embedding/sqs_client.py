"""
Thin SQS wrapper for the embedding queue.

We keep this small on purpose - the worker only needs three
operations: long-poll receive, delete (ack), send (used by the
backfill CLI). Anything more elaborate belongs in the worker module.

Payload shape (ARCHITECTURE.md s7):

    {
      "record_type":             "moment" | "entity" | "thread" | "trait" | "question",
      "record_id":               "<uuid>",
      "source_text":             "<the actual text to embed>",
      "embedding_model":         "voyage-3-large",
      "embedding_model_version": "<opaque tag>"
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flashback.queues.boto import make_sqs_client


@dataclass(frozen=True)
class EmbeddingMessage:
    """One message pulled off the embedding queue."""

    record_type: str
    record_id: str
    source_text: str
    embedding_model: str
    embedding_model_version: str
    receipt_handle: str
    raw_body: str


def _parse_message(msg: dict) -> EmbeddingMessage:
    body = json.loads(msg["Body"])
    return EmbeddingMessage(
        record_type=body["record_type"],
        record_id=body["record_id"],
        source_text=body["source_text"],
        embedding_model=body["embedding_model"],
        embedding_model_version=body["embedding_model_version"],
        receipt_handle=msg["ReceiptHandle"],
        raw_body=msg["Body"],
    )


@dataclass
class SQSClient:
    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def receive(
        self, *, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[EmbeddingMessage]:
        """Long-poll the queue and return up to ``max_messages`` parsed messages."""
        resp = self._get_client().receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
        )
        return [_parse_message(m) for m in resp.get("Messages", [])]

    def delete(self, receipt_handle: str) -> None:
        """Ack a single message."""
        self._get_client().delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )

    def send_embedding_job(
        self,
        *,
        record_type: str,
        record_id: str,
        source_text: str,
        embedding_model: str,
        embedding_model_version: str,
    ) -> None:
        """Publish a single embedding job. Used by the backfill CLI."""
        payload = {
            "record_type": record_type,
            "record_id": record_id,
            "source_text": source_text,
            "embedding_model": embedding_model,
            "embedding_model_version": embedding_model_version,
        }
        self._get_client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
        )
