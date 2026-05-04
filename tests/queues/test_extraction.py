from __future__ import annotations

from uuid import UUID, uuid4

from flashback.queues.extraction import ExtractionQueueProducer
from tests.segment_detector.fixtures.sample_segments import SAMPLE_SEGMENT


class CapturingSQS:
    def __init__(self) -> None:
        self.queue_url = None
        self.body = None

    async def send_message(self, queue_url: str, body: dict) -> str:
        self.queue_url = queue_url
        self.body = body
        return "msg-456"


async def test_extraction_push_uses_architecture_payload_shape():
    sqs = CapturingSQS()
    producer = ExtractionQueueProducer(sqs, "queue-url")
    session_id = uuid4()
    person_id = uuid4()
    question_id = UUID("55555555-5555-5555-5555-555555555555")

    message_id = await producer.push(
        session_id=session_id,
        person_id=person_id,
        segment_turns=SAMPLE_SEGMENT,
        rolling_summary="New summary.",
        prior_rolling_summary="Old summary.",
        seeded_question_id=question_id,
    )

    assert message_id == "msg-456"
    assert sqs.queue_url == "queue-url"
    assert sqs.body == {
        "session_id": str(session_id),
        "person_id": str(person_id),
        "segment_turns": [
            turn.model_dump(mode="json") for turn in SAMPLE_SEGMENT
        ],
        "rolling_summary": "New summary.",
        "prior_rolling_summary": "Old summary.",
        "seeded_question_id": str(question_id),
    }


async def test_extraction_push_serializes_missing_seeded_question_as_null():
    sqs = CapturingSQS()
    producer = ExtractionQueueProducer(sqs, "queue-url")

    await producer.push(
        session_id=uuid4(),
        person_id=uuid4(),
        segment_turns=SAMPLE_SEGMENT,
        rolling_summary="New summary.",
        prior_rolling_summary="",
        seeded_question_id=None,
    )

    assert sqs.body["seeded_question_id"] is None


async def test_extraction_push_serializes_turns_as_json_ready_objects():
    sqs = CapturingSQS()
    producer = ExtractionQueueProducer(sqs, "queue-url")

    await producer.push(
        session_id=uuid4(),
        person_id=uuid4(),
        segment_turns=SAMPLE_SEGMENT,
        rolling_summary="New summary.",
        prior_rolling_summary="",
        seeded_question_id=None,
    )

    first_turn = sqs.body["segment_turns"][0]
    assert set(first_turn) == {"role", "content", "timestamp"}
    assert first_turn["role"] == "user"
    assert isinstance(first_turn["timestamp"], str)
