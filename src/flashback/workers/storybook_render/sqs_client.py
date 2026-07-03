"""Thin SQS wrapper for the storybook_render queue (long-poll receive + ack).

Trigger-only payload (queues.storybook_render):
    {"job_id", "storybook_id", "person_id", "composed_at", "enqueued_at"}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flashback.queues.boto import make_sqs_client


@dataclass(frozen=True)
class StorybookRenderMessage:
    job_id: str
    storybook_id: str
    person_id: str
    composed_at: str
    receipt_handle: str
    raw_body: str
    receive_count: int = 1  # SQS ApproximateReceiveCount (1 on first delivery)


def _parse_message(msg: dict) -> StorybookRenderMessage:
    body = json.loads(msg["Body"])
    attrs = msg.get("Attributes", {})
    return StorybookRenderMessage(
        job_id=body.get("job_id", ""),
        storybook_id=body["storybook_id"],
        person_id=body.get("person_id", ""),
        composed_at=body.get("composed_at", ""),
        receipt_handle=msg["ReceiptHandle"],
        raw_body=msg["Body"],
        receive_count=int(attrs.get("ApproximateReceiveCount", "1")),
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

    def receive(self, *, max_messages: int = 1,
                wait_seconds: int = 20) -> list[StorybookRenderMessage]:
        resp = self._get_client().receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [_parse_message(m) for m in resp.get("Messages", [])]

    def delete(self, receipt_handle: str) -> None:
        self._get_client().delete_message(
            QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
