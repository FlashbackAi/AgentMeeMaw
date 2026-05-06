"""SQS clients for the Thread Detector queue.

Sibling to :mod:`flashback.workers.extraction.sqs_client`: sync,
``boto3``-based, no async. Two concerns live here:

* :class:`ThreadDetectorJobSender` — push trigger jobs. Used by the
  Extraction Worker post-commit (see ``thread_trigger.py``) and by
  any future producer.
* :class:`ThreadDetectorSQSClient` — receive and ack messages on the
  ``thread_detector`` queue. Used by the Thread Detector worker drain
  loop.

Inbound message body shape (set by
``check_and_push_thread_detector_trigger``)::

    {
        "person_id":              "<uuid>",
        "active_count_at_trigger": <int>,
        "last_count_at_trigger":   <int>
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from flashback.queues.boto import make_sqs_client

from .schema import ThreadDetectorMessage

log = structlog.get_logger("flashback.workers.thread_detector.sqs_client")


@dataclass(frozen=True)
class ReceivedThreadDetectorMessage:
    """One inbound thread-detector message plus SQS bookkeeping."""

    message_id: str
    receipt_handle: str
    payload: ThreadDetectorMessage
    raw_body: str


@dataclass
class ThreadDetectorJobSender:
    """Producer for the ``thread_detector`` queue."""

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
        person_id: str,
        active_count_at_trigger: int,
        last_count_at_trigger: int,
    ) -> str:
        payload = {
            "person_id": person_id,
            "active_count_at_trigger": active_count_at_trigger,
            "last_count_at_trigger": last_count_at_trigger,
        }
        resp = self._get_client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
        )
        return str(resp["MessageId"])


@dataclass
class ThreadDetectorSQSClient:
    """Consumer for the ``thread_detector`` queue."""

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = make_sqs_client(self.region_name)
        return self._client

    def receive(
        self, *, wait_seconds: int = 20
    ) -> list[ReceivedThreadDetectorMessage]:
        """Long-poll for a single thread-detector message."""
        resp = self._get_client().receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
        )
        out: list[ReceivedThreadDetectorMessage] = []
        for msg in resp.get("Messages", []) or []:
            body = msg["Body"]
            try:
                payload = ThreadDetectorMessage.model_validate_json(body)
            except ValidationError as exc:
                log.error(
                    "thread_detector.malformed_message_acking",
                    message_id=msg.get("MessageId"),
                    error=str(exc),
                )
                self.delete(msg["ReceiptHandle"])
                continue
            out.append(
                ReceivedThreadDetectorMessage(
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
