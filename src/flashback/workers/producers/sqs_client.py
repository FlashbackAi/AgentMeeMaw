"""SQS clients for the question producer queues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from flashback.queues.boto import make_sqs_client

from .schema import ProducerMessage

log = structlog.get_logger("flashback.workers.producers.sqs_client")


@dataclass(frozen=True)
class ReceivedProducerMessage:
    message_id: str
    receipt_handle: str
    payload: ProducerMessage
    raw_body: str


@dataclass
class ProducerJobSender:
    """Producer for either the per-session or weekly producer queue."""

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def send(self, *, person_id: str, producer: str) -> str:
        payload = {"person_id": person_id, "producer": producer}
        resp = self._get_client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
        )
        return str(resp["MessageId"])


@dataclass
class ProducerSQSClient:
    """Consumer for one of the producer queues."""

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def receive(self, *, wait_seconds: int = 20) -> list[ReceivedProducerMessage]:
        resp = self._get_client().receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
        )
        out: list[ReceivedProducerMessage] = []
        for msg in resp.get("Messages", []) or []:
            body = msg["Body"]
            try:
                payload = ProducerMessage.model_validate_json(body)
            except ValidationError as exc:
                log.error(
                    "producer.malformed_message_acking",
                    message_id=msg.get("MessageId"),
                    error=str(exc),
                )
                self.delete(msg["ReceiptHandle"])
                continue
            out.append(
                ReceivedProducerMessage(
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
