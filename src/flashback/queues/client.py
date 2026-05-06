"""Async SQS wrapper for HTTP-side queue producers."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class QueueError(RuntimeError):
    """Base class for queue producer failures."""


class QueueSendError(QueueError):
    """Raised when SQS send_message fails."""


class AsyncSQSClient:
    """
    Async wrapper over sync boto3 SQS.

    The HTTP service sends small producer messages. For v1, running
    boto3's ``send_message`` in a worker thread avoids another AWS
    dependency while keeping the turn handler async-friendly.
    """

    def __init__(self, sqs_client: Any):
        self._sqs = sqs_client

    async def send_message(self, queue_url: str, body: dict) -> str:
        """Return the SQS MessageId. Underlying SQS exceptions propagate."""

        return await asyncio.to_thread(self._send_sync, queue_url, body)

    async def get_queue_attributes(self, queue_url: str) -> dict[str, Any]:
        """Return a small attribute snapshot for health checks."""
        return await asyncio.to_thread(self._get_queue_attributes_sync, queue_url)

    def _send_sync(self, queue_url: str, body: dict) -> str:
        resp = self._sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(body),
        )
        return str(resp["MessageId"])

    def _get_queue_attributes_sync(self, queue_url: str) -> dict[str, Any]:
        resp = self._sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"],
        )
        return dict(resp.get("Attributes", {}))
