from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import importlib

# steps/__init__ re-exports the function under the same name, shadowing
# the submodule attribute — resolve the module explicitly for patching.
step_mod = importlib.import_module(
    "flashback.orchestrator.steps.select_ground_truth_tap"
)
from flashback.intent_classifier.schema import IntentResult
from flashback.orchestrator.state import TurnState
from flashback.working_memory.schema import Turn, WorkingMemoryState


def _turn(role, content):
    return Turn(role=role, content=content,
                timestamp=datetime(2026, 6, 11, tzinfo=timezone.utc))


def _state(intent="story", temperature="medium", user_turns=10):
    state = TurnState(
        turn_id=uuid4(), session_id=uuid4(), person_id=uuid4(),
        role_id=uuid4(), user_message="...",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    state.intent_result = IntentResult(
        intent=intent, confidence="high",
        emotional_temperature=temperature, reasoning="",
    )
    state.effective_temperature = temperature
    state.transcript = [_turn("user", f"m{i}") for i in range(user_turns)]
    return state


def _wm_state(**overrides):
    base = dict(
        person_id="p", role_id="r",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return WorkingMemoryState(**base)


def _deps(wm_state):
    wm = AsyncMock()
    wm.get_state.return_value = wm_state
    wm.get_transcript.return_value = []
    return SimpleNamespace(
        working_memory=wm, db_pool=object(), settings=SimpleNamespace()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["switch", "clarify", "recall"])
async def test_skips_on_non_story_intents(intent, monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state(intent=intent)
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_on_high_temperature(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state(temperature="high")
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_session_cap_reached(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state()
    deps = _deps(
        _wm_state(
            gt_taps_emitted_this_session=step_mod.GT_TAPS_PER_SESSION_CAP
        )
    )
    await step_mod.select_ground_truth_tap(state, deps)
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_during_tap_cooldown(monkeypatch):
    """A tap fired on the previous user turn — never two back-to-back."""
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state()
    deps = _deps(_wm_state(user_turns_since_last_tap=1))
    await step_mod.select_ground_truth_tap(state, deps)
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_before_three_user_turns(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state(user_turns=2)
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_another_tap_pending(monkeypatch):
    from flashback.orchestrator.protocol import Tap
    state = _state()
    state.taps = [Tap(question_id=uuid4(), text="q", dimension="sensory")]
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert len(state.taps) == 1  # untouched


@pytest.mark.asyncio
async def test_emits_field_tap_and_records_pending(monkeypatch):
    monkeypatch.setattr(
        step_mod, "fetch_ground_truth", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        step_mod,
        "select_ground_truth_question",
        AsyncMock(return_value={
            "action": "ask_field", "field": "region",
            "question_text": "Where was that house?",
            "options": ["Karimnagar", "Hyderabad", "Another town", "Abroad"],
        }),
    )
    state = _state()
    deps = _deps(_wm_state())
    await step_mod.select_ground_truth_tap(state, deps)
    assert len(state.taps) == 1
    tap = state.taps[0]
    assert tap.kind == "ground_truth"
    assert tap.field == "region"
    assert tap.question_id is None
    deps.working_memory.record_gt_tap_emitted.assert_awaited_once()


@pytest.mark.asyncio
async def test_anchor_tap_uses_derived_chips_when_birth_era_known(monkeypatch):
    gt = {"birth_era": {"value": "1950s", "provenance": "tap",
                        "confidence": "high", "updated_at": "x"}}
    monkeypatch.setattr(
        step_mod, "fetch_ground_truth", AsyncMock(return_value=gt)
    )
    monkeypatch.setattr(
        step_mod,
        "select_ground_truth_question",
        AsyncMock(return_value={
            "action": "ask_anchor",
            "question_text": "About when was that?",
            "options": ["a", "b", "c", "d"],
        }),
    )
    state = _state()
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps[0].kind == "segment_anchor"
    assert state.taps[0].options == [
        "When they were young", "In the 1970s", "In the 1980s", "Later in life",
    ]


@pytest.mark.asyncio
async def test_llm_skip_means_no_tap(monkeypatch):
    monkeypatch.setattr(
        step_mod, "fetch_ground_truth", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        step_mod, "select_ground_truth_question", AsyncMock(return_value=None)
    )
    state = _state()
    deps = _deps(_wm_state())
    await step_mod.select_ground_truth_tap(state, deps)
    assert state.taps == []
    deps.working_memory.record_gt_tap_emitted.assert_not_awaited()
