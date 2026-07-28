from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flashback.queues.extraction import ExtractionQueueProducer
from flashback.workers.extraction.schema import ExtractionMessage


@pytest.mark.asyncio
async def test_push_includes_segment_anchor_when_present():
    sqs = AsyncMock()
    sqs.send_message.return_value = "msg-1"
    producer = ExtractionQueueProducer(sqs, "https://queue")
    await producer.push(
        session_id=uuid4(), person_id=uuid4(), segment_turns=[],
        rolling_summary="", prior_rolling_summary="",
        seeded_question_id=None,
        segment_anchor={"question_text": "About when?", "answer": "1970s"},
    )
    payload = sqs.send_message.await_args.args[1]
    assert payload["segment_anchor"] == {
        "question_text": "About when?", "answer": "1970s"
    }


@pytest.mark.asyncio
async def test_push_defaults_segment_anchor_to_none():
    sqs = AsyncMock()
    sqs.send_message.return_value = "msg-1"
    producer = ExtractionQueueProducer(sqs, "https://queue")
    await producer.push(
        session_id=uuid4(), person_id=uuid4(), segment_turns=[],
        rolling_summary="", prior_rolling_summary="",
        seeded_question_id=None,
    )
    payload = sqs.send_message.await_args.args[1]
    assert payload["segment_anchor"] is None


def test_extraction_message_parses_segment_anchor():
    msg = ExtractionMessage.model_validate({
        "session_id": str(uuid4()), "person_id": str(uuid4()),
        "segment_turns": [],
        "segment_anchor": {"question_text": "About when?", "answer": "1970s"},
    })
    assert msg.segment_anchor is not None
    assert msg.segment_anchor.answer == "1970s"


def test_extraction_message_tolerates_missing_anchor():
    msg = ExtractionMessage.model_validate({
        "session_id": str(uuid4()), "person_id": str(uuid4()),
        "segment_turns": [],
    })
    assert msg.segment_anchor is None
