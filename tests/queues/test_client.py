from __future__ import annotations

import json

import pytest

from flashback.queues.client import AsyncSQSClient


class FakeSQS:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return {"MessageId": "msg-123"}


async def test_send_message_returns_message_id():
    sqs = FakeSQS()
    client = AsyncSQSClient(sqs)

    message_id = await client.send_message("queue-url", {"hello": "world"})

    assert message_id == "msg-123"


async def test_send_message_propagates_original_exception():
    exc = RuntimeError("sqs down")
    client = AsyncSQSClient(FakeSQS(raises=exc))

    with pytest.raises(RuntimeError) as raised:
        await client.send_message("queue-url", {"hello": "world"})

    assert raised.value is exc


async def test_send_message_json_serializes_body():
    sqs = FakeSQS()
    client = AsyncSQSClient(sqs)

    await client.send_message("queue-url", {"hello": "world"})

    assert sqs.calls[0]["QueueUrl"] == "queue-url"
    assert json.loads(sqs.calls[0]["MessageBody"]) == {"hello": "world"}
