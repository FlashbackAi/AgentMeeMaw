"""SQS clients for the Profile Summary queue.

Sibling to :mod:`flashback.workers.trait_synthesizer.sqs_client`: sync,
``boto3``-based, no async. Two concerns:

* :class:`ProfileSummaryJobSender` — push trigger jobs. The producer
  is Session Wrap (step 16, not yet wired). Built here so step 16 has
  somewhere to import from when it lands.
* :class:`ProfileSummarySQSClient` — receive and ack messages on the
  ``profile_summary`` queue. Used by the worker drain loop.

Inbound message body shape::

    {
        "person_id": "<uuid>"
    }

That's the whole payload. The worker rebuilds context from the
canonical graph at processing time, so the queue body stays small and
re-deliveries always operate on current state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import boto3

from .schema import ProfileSummaryMessage


@dataclass(frozen=True)
class ReceivedProfileSummaryMessage:
    """One inbound profile-summary message plus SQS bookkeeping."""

    message_id: str
    receipt_handle: str
    payload: ProfileSummaryMessage
    raw_body: str


@dataclass
class ProfileSummaryJobSender:
    """Producer for the ``profile_summary`` queue."""

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("sqs", region_name=self.region_name)
        return self._client

    def send(self, *, person_id: str) -> str:
        payload = {"person_id": person_id}
        resp = self._get_client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
        )
        return str(resp["MessageId"])


@dataclass
class ProfileSummarySQSClient:
    """Consumer for the ``profile_summary`` queue."""

    queue_url: str
    region_name: str
    _client: Any | None = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("sqs", region_name=self.region_name)
        return self._client

    def receive(
        self, *, wait_seconds: int = 20
    ) -> list[ReceivedProfileSummaryMessage]:
        """Long-poll for a single profile-summary message."""
        resp = self._get_client().receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
        )
        out: list[ReceivedProfileSummaryMessage] = []
        for msg in resp.get("Messages", []) or []:
            body = msg["Body"]
            payload = ProfileSummaryMessage.model_validate_json(body)
            out.append(
                ReceivedProfileSummaryMessage(
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
