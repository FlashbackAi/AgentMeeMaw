import json
from datetime import datetime, timezone

import pytest

from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def _state(**overrides):
    base = dict(
        person_id="p1", role_id="r1",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return WorkingMemoryState(**base)


def test_new_fields_default_and_roundtrip():
    state = _state()
    assert state.gt_taps_emitted_this_session == 0
    assert state.signal_pending_gt_tap == ""
    assert state.gt_declined_fields == []
    assert state.segment_anchor_question == ""
    assert state.segment_anchor_answer == ""

    raw = serialise_state_for_init(state)
    parsed = parse_state_hash(raw)
    assert parsed.gt_taps_emitted_this_session == 0
    assert parsed.gt_declined_fields == []


def test_parse_state_hash_decodes_gt_fields():
    raw = serialise_state_for_init(_state())
    raw["gt_taps_emitted_this_session"] = "1"
    raw["gt_declined_fields"] = json.dumps(["attire"])
    raw["signal_pending_gt_tap"] = json.dumps(
        {"kind": "ground_truth", "field": "region", "question_text": "Where?"}
    )
    parsed = parse_state_hash(raw)
    assert parsed.gt_taps_emitted_this_session == 1
    assert parsed.gt_declined_fields == ["attire"]
    assert json.loads(parsed.signal_pending_gt_tap)["field"] == "region"


@pytest.mark.asyncio
async def test_client_gt_tap_lifecycle(wm):
    session_id = "sess-gt-1"
    await wm.initialize(
        session_id=session_id, person_id="p1", role_id="r1",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    payload = json.dumps(
        {"kind": "ground_truth", "field": "region", "question_text": "Where?"}
    )
    await wm.record_gt_tap_emitted(
        session_id=session_id, payload_json=payload, question_text="Where?"
    )
    state = await wm.get_state(session_id)
    assert state.gt_taps_emitted_this_session == 1
    assert state.signal_pending_gt_tap == payload
    assert state.signal_pending_tap_question == "Where?"
    assert state.user_turns_since_last_tap == 0

    await wm.add_gt_declined_field(session_id, "region")
    await wm.clear_pending_gt_tap(session_id)
    state = await wm.get_state(session_id)
    assert state.gt_declined_fields == ["region"]
    assert state.signal_pending_gt_tap == ""


@pytest.mark.asyncio
async def test_client_segment_anchor_lifecycle(wm):
    session_id = "sess-gt-2"
    await wm.initialize(
        session_id=session_id, person_id="p1", role_id="r1",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    await wm.set_segment_anchor(
        session_id, question_text="About when was that?", answer="In the 1970s"
    )
    state = await wm.get_state(session_id)
    assert state.segment_anchor_question == "About when was that?"
    assert state.segment_anchor_answer == "In the 1970s"

    await wm.clear_segment_anchor(session_id)
    state = await wm.get_state(session_id)
    assert state.segment_anchor_answer == ""
