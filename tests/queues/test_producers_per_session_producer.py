from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.queues.producers_per_session import ProducersPerSessionQueueProducer


class CapturingSQS:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.queue_url = None
        self.body = None

    async def send_message(self, queue_url: str, body: dict) -> str:
        self.queue_url = queue_url
        self.body = body
        if self.raises:
            raise self.raises
        return "msg-p2"


async def test_push_payload_shape_and_message_id():
    sqs = CapturingSQS()
    producer = ProducersPerSessionQueueProducer(sqs, "p2-url")
    session_id = uuid4()
    person_id = uuid4()

    msg_id = await producer.push(person_id=person_id, session_id=session_id)

    assert msg_id == "msg-p2"
    assert sqs.queue_url == "p2-url"
    assert sqs.body == {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "idempotency_key": str(session_id),
        "producer": "P2",
        "triggered_by": "session_wrap",
    }


async def test_push_propagates_sqs_errors():
    exc = RuntimeError("sqs down")
    producer = ProducersPerSessionQueueProducer(CapturingSQS(exc), "p2-url")

    with pytest.raises(RuntimeError) as raised:
        await producer.push(person_id=uuid4(), session_id=uuid4())

    assert raised.value is exc
