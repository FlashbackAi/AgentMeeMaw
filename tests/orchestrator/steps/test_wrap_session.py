from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from flashback.llm.errors import LLMError
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.state import SessionWrapState
from flashback.orchestrator.steps import wrap_session as wrap_module
from flashback.orchestrator.steps.starter_opener import PersonRow
from flashback.segment_detector.schema import SegmentDetectionResult
from flashback.session_summary.schema import SessionSummaryResult
from tests.segment_detector.fixtures.sample_segments import SAMPLE_SEGMENT


class FakeWorkingMemory:
    def __init__(
        self,
        *,
        segment_turns=None,
        rolling_summary="Old summary.",
        clear_raises: Exception | None = None,
        contributor_display_name: str = "",
    ) -> None:
        self.segment_turns = list(segment_turns or [])
        self.clear_raises = clear_raises
        self.state = SimpleNamespace(
            rolling_summary=rolling_summary,
            last_seeded_question_id="",
            segments_pushed_this_session=0,
            contributor_display_name=contributor_display_name,
        )
        self.updated_summary = None
        self.reset_calls = 0
        self.seeded_updates = []
        self.clear_calls = 0

    async def get_state(self, session_id: str):
        return self.state

    async def get_segment(self, session_id: str):
        return self.segment_turns

    async def get_contributor_display_name(self, session_id: str) -> str:
        return self.state.contributor_display_name

    async def update_rolling_summary(self, session_id: str, new_summary: str):
        self.updated_summary = new_summary
        self.state.rolling_summary = new_summary

    async def reset_segment(self, session_id: str):
        self.reset_calls += 1
        turns = self.segment_turns
        self.segment_turns = []
        return turns

    async def set_seeded_question(self, session_id: str, question_id: str | None):
        self.seeded_updates.append(question_id)
        self.state.last_seeded_question_id = question_id or ""

    async def increment_segments_pushed(self, session_id: str):
        self.state.segments_pushed_this_session += 1
        return self.state.segments_pushed_this_session

    async def clear(self, session_id: str):
        self.clear_calls += 1
        if self.clear_raises:
            raise self.clear_raises


class FakeDetector:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls = []

    async def detect(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return SegmentDetectionResult(
            boundary_detected=True,
            rolling_summary="New summary.",
            reasoning="forced close",
        )


class FakeExtractionQueue:
    def __init__(self) -> None:
        self.calls = []

    async def push(self, **kwargs):
        self.calls.append(kwargs)
        return "msg-extract"


class FakeSummaryGenerator:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls = []

    async def generate(self, ctx):
        self.calls.append(ctx)
        if self.raises:
            raise self.raises
        return SessionSummaryResult(text="the lake cabin")


class FakeQueue:
    def __init__(self, name: str, raises: Exception | None = None) -> None:
        self.name = name
        self.raises = raises
        self.calls = []

    async def push(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return f"msg-{self.name}"


class BarrierQueue(FakeQueue):
    active = 0
    event: asyncio.Event

    async def push(self, **kwargs):
        self.calls.append(kwargs)
        BarrierQueue.active += 1
        if BarrierQueue.active == 3:
            BarrierQueue.event.set()
        await BarrierQueue.event.wait()
        return f"msg-{self.name}"


def _state() -> SessionWrapState:
    return SessionWrapState(
        session_id=uuid4(),
        person_id=uuid4(),
        started_at=datetime.now(timezone.utc),
    )


def _deps(
    *,
    wm,
    detector=None,
    extraction_queue=None,
    summary=None,
    trait=None,
    profile=None,
    producers=None,
) -> OrchestratorDeps:
    return OrchestratorDeps(
        db_pool=None,
        working_memory=wm,
        intent_classifier=None,
        retrieval=None,
        phase_gate=None,
        response_generator=None,
        segment_detector=detector or FakeDetector(),
        extraction_queue=extraction_queue or FakeExtractionQueue(),
        session_summary_generator=summary or FakeSummaryGenerator(),
        trait_synthesizer_queue=trait or FakeQueue("trait"),
        profile_summary_queue=profile or FakeQueue("profile"),
        producers_per_session_queue=producers or FakeQueue("p2"),
    )


def _patch_person(monkeypatch):
    monkeypatch.setattr(
        wrap_module,
        "fetch_person",
        AsyncMock(return_value=PersonRow("Maya", "mother", "starter")),
    )


async def test_force_close_happy_path(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()
    wm = FakeWorkingMemory(segment_turns=SAMPLE_SEGMENT)
    detector = FakeDetector()
    extraction_queue = FakeExtractionQueue()

    await wrap_module.wrap_session(
        state,
        _deps(wm=wm, detector=detector, extraction_queue=extraction_queue),
    )

    assert detector.calls[0]["force"] is True
    assert extraction_queue.calls[0]["rolling_summary"] == "New summary."
    assert wm.updated_summary == "New summary."
    assert wm.reset_calls == 1
    assert wm.seeded_updates == [None]
    assert state.final_segment_pushed is True
    assert state.segments_pushed_count == 1


async def test_no_open_segment_does_not_push_extraction(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()
    wm = FakeWorkingMemory(segment_turns=[])
    detector = FakeDetector()
    extraction_queue = FakeExtractionQueue()

    await wrap_module.wrap_session(
        state,
        _deps(wm=wm, detector=detector, extraction_queue=extraction_queue),
    )

    assert detector.calls == []
    assert extraction_queue.calls == []
    assert state.final_segment_pushed is False


async def test_session_summary_happy_path(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()
    summary = FakeSummaryGenerator()

    await wrap_module.wrap_session(
        state,
        _deps(wm=FakeWorkingMemory(segment_turns=[]), summary=summary),
    )

    assert state.session_summary_text == "the lake cabin"
    assert summary.calls[0].person_name == "Maya"


async def test_session_summary_failure_degrades(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()

    await wrap_module.wrap_session(
        state,
        _deps(
            wm=FakeWorkingMemory(segment_turns=[]),
            summary=FakeSummaryGenerator(raises=LLMError("summary down")),
        ),
    )

    assert state.session_summary_text == ""
    assert "generate_session_summary" in state.failures


async def test_three_queue_pushes_happen_in_parallel(monkeypatch):
    _patch_person(monkeypatch)
    BarrierQueue.active = 0
    BarrierQueue.event = asyncio.Event()
    state = _state()

    await asyncio.wait_for(
        wrap_module.wrap_session(
            state,
            _deps(
                wm=FakeWorkingMemory(segment_turns=[]),
                trait=BarrierQueue("trait"),
                profile=BarrierQueue("profile"),
                producers=BarrierQueue("p2"),
            ),
        ),
        timeout=1,
    )

    assert state.trait_synthesizer_pushed is True
    assert state.profile_summary_pushed is True
    assert state.producers_per_session_pushed is True


async def test_one_queue_failure_does_not_block_others(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()
    trait = FakeQueue("trait", raises=RuntimeError("trait down"))
    profile = FakeQueue("profile")
    producers = FakeQueue("p2")

    await wrap_module.wrap_session(
        state,
        _deps(
            wm=FakeWorkingMemory(segment_turns=[]),
            trait=trait,
            profile=profile,
            producers=producers,
        ),
    )

    assert "push_trait_synthesizer" in state.failures
    assert state.profile_summary_pushed is True
    assert state.producers_per_session_pushed is True


async def test_all_queue_failures_degrade(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()

    await wrap_module.wrap_session(
        state,
        _deps(
            wm=FakeWorkingMemory(segment_turns=[]),
            trait=FakeQueue("trait", raises=RuntimeError("trait down")),
            profile=FakeQueue("profile", raises=RuntimeError("profile down")),
            producers=FakeQueue("p2", raises=RuntimeError("p2 down")),
        ),
    )

    assert {
        "push_trait_synthesizer",
        "push_profile_summary",
        "push_producers",
    }.issubset(state.failures)


async def test_contributor_display_name_propagates_to_all_queues(monkeypatch):
    """Each archive-side queue push carries the contributor's display
    name from working memory."""
    _patch_person(monkeypatch)
    state = _state()
    trait = FakeQueue("trait")
    profile = FakeQueue("profile")
    extraction_queue = FakeExtractionQueue()

    await wrap_module.wrap_session(
        state,
        _deps(
            wm=FakeWorkingMemory(
                segment_turns=SAMPLE_SEGMENT,
                contributor_display_name="Sarah",
            ),
            extraction_queue=extraction_queue,
            trait=trait,
            profile=profile,
        ),
    )

    assert extraction_queue.calls[0]["contributor_display_name"] == "Sarah"
    assert trait.calls[0]["contributor_display_name"] == "Sarah"
    assert profile.calls[0]["contributor_display_name"] == "Sarah"


async def test_clear_failure_degrades(monkeypatch):
    _patch_person(monkeypatch)
    state = _state()

    await wrap_module.wrap_session(
        state,
        _deps(wm=FakeWorkingMemory(segment_turns=[], clear_raises=RuntimeError("nope"))),
    )

    assert "clear_wm" in state.failures
