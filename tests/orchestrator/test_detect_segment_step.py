from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from flashback.llm.errors import LLMError
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.state import TurnState
from flashback.orchestrator.steps.detect_segment import detect_segment
from flashback.queues.client import QueueSendError
from flashback.segment_detector.schema import SegmentDetectionResult
from tests.segment_detector.fixtures.sample_segments import SAMPLE_SEGMENT


class FakeWorkingMemory:
    def __init__(
        self,
        *,
        segment_turns=None,
        rolling_summary: str = "Old summary.",
        seeded_question_id: str = "",
    ) -> None:
        self.segment_turns = list(segment_turns or [])
        self.state = SimpleNamespace(
            rolling_summary=rolling_summary,
            last_seeded_question_id=seeded_question_id,
        )
        self.updated_summary = None
        self.reset_calls = 0
        self.seeded_question_updates = []

    async def get_segment(self, session_id: str):
        return self.segment_turns

    async def get_state(self, session_id: str):
        return self.state

    async def update_rolling_summary(self, session_id: str, new_summary: str):
        self.updated_summary = new_summary

    async def reset_segment(self, session_id: str):
        self.reset_calls += 1
        turns = self.segment_turns
        self.segment_turns = []
        return turns

    async def set_seeded_question(self, session_id: str, question_id: str | None):
        self.seeded_question_updates.append(question_id)


class FakeDetector:
    def __init__(self, *, result=None, raises: Exception | None = None) -> None:
        self.result = result or SegmentDetectionResult(
            boundary_detected=False,
            reasoning="Still ongoing.",
        )
        self.raises = raises
        self.calls = []

    async def detect(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return self.result


class FakeExtractionQueue:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls = []

    async def push(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return "msg-789"


def _state() -> TurnState:
    return TurnState(
        turn_id=uuid4(),
        session_id=uuid4(),
        person_id=uuid4(),
        role_id=uuid4(),
        user_message="Tell me more.",
        started_at=datetime.now(timezone.utc),
    )


def _deps(
    *,
    wm,
    detector=None,
    queue=None,
    threshold: int = 4,
) -> OrchestratorDeps:
    return OrchestratorDeps(
        db_pool=None,
        working_memory=wm,
        intent_classifier=None,
        retrieval=None,
        phase_gate=None,
        response_generator=None,
        segment_detector=detector or FakeDetector(),
        extraction_queue=queue or FakeExtractionQueue(),
        settings=SimpleNamespace(segment_detector_min_turns=threshold),
    )


async def test_below_threshold_is_noop():
    wm = FakeWorkingMemory(segment_turns=SAMPLE_SEGMENT[:2])
    detector = FakeDetector()
    queue = FakeExtractionQueue()

    await detect_segment(_state(), _deps(wm=wm, detector=detector, queue=queue))

    assert detector.calls == []
    assert queue.calls == []
    assert wm.updated_summary is None
    assert wm.reset_calls == 0


async def test_at_threshold_no_boundary_leaves_wm_alone():
    wm = FakeWorkingMemory(segment_turns=SAMPLE_SEGMENT)
    detector = FakeDetector(
        result=SegmentDetectionResult(
            boundary_detected=False,
            reasoning="The contributor is mid-story.",
        )
    )
    queue = FakeExtractionQueue()

    await detect_segment(_state(), _deps(wm=wm, detector=detector, queue=queue))

    assert len(detector.calls) == 1
    assert queue.calls == []
    assert wm.updated_summary is None
    assert wm.reset_calls == 0


async def test_boundary_pushes_queue_then_updates_wm_and_state():
    state = _state()
    wm = FakeWorkingMemory(segment_turns=SAMPLE_SEGMENT)
    detector = FakeDetector(
        result=SegmentDetectionResult(
            boundary_detected=True,
            rolling_summary="New summary.",
            reasoning="The topic has wrapped.",
        )
    )
    queue = FakeExtractionQueue()

    await detect_segment(state, _deps(wm=wm, detector=detector, queue=queue))

    assert queue.calls[0]["session_id"] == state.session_id
    assert queue.calls[0]["person_id"] == state.person_id
    assert queue.calls[0]["rolling_summary"] == "New summary."
    assert queue.calls[0]["prior_rolling_summary"] == "Old summary."
    assert wm.updated_summary == "New summary."
    assert wm.reset_calls == 1
    assert wm.seeded_question_updates == [None]
    assert state.segment_boundary_detected is True


async def test_sqs_push_failure_does_not_mutate_wm():
    wm = FakeWorkingMemory(segment_turns=SAMPLE_SEGMENT)
    detector = FakeDetector(
        result=SegmentDetectionResult(
            boundary_detected=True,
            rolling_summary="New summary.",
            reasoning="The topic has wrapped.",
        )
    )
    queue = FakeExtractionQueue(raises=RuntimeError("sqs down"))

    with pytest.raises(QueueSendError):
        await detect_segment(_state(), _deps(wm=wm, detector=detector, queue=queue))

    assert wm.updated_summary is None
    assert wm.reset_calls == 0
    assert wm.seeded_question_updates == []


async def test_llm_failure_does_not_push_or_mutate_wm():
    wm = FakeWorkingMemory(segment_turns=SAMPLE_SEGMENT)
    detector = FakeDetector(raises=LLMError("detector down"))
    queue = FakeExtractionQueue()

    with pytest.raises(LLMError):
        await detect_segment(_state(), _deps(wm=wm, detector=detector, queue=queue))

    assert queue.calls == []
    assert wm.updated_summary is None
    assert wm.reset_calls == 0


async def test_seeded_question_id_flows_into_payload():
    seeded_id = UUID("55555555-5555-5555-5555-555555555555")
    wm = FakeWorkingMemory(
        segment_turns=SAMPLE_SEGMENT,
        seeded_question_id=str(seeded_id),
    )
    detector = FakeDetector(
        result=SegmentDetectionResult(
            boundary_detected=True,
            rolling_summary="New summary.",
            reasoning="The topic has wrapped.",
        )
    )
    queue = FakeExtractionQueue()

    await detect_segment(_state(), _deps(wm=wm, detector=detector, queue=queue))

    assert queue.calls[0]["seeded_question_id"] == seeded_id
