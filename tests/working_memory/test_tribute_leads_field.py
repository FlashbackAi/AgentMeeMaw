"""The tribute_leads WM field round-trips and mark-pursued mutates it."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from flashback.tribute.leads import build_leads, leads_to_json, pick_next_lead
from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def test_tribute_leads_defaults_empty_and_round_trips() -> None:
    state = WorkingMemoryState(
        person_id="p1",
        role_id="r1",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert state.tribute_leads == ""

    leads_json = leads_to_json(
        build_leads([{"question_id": "q10", "option_label": "Sold a home"}])
    )
    state2 = WorkingMemoryState(
        person_id="p1",
        role_id="r1",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tribute_leads=leads_json,
    )
    raw = serialise_state_for_init(state2)
    assert raw["tribute_leads"] == leads_json
    assert parse_state_hash(raw).tribute_leads == leads_json


@pytest.mark.asyncio
async def test_mark_tribute_lead_pursued_flips_in_valkey(wm) -> None:
    session_id = "sess-leads-1"
    leads_json = leads_to_json(
        build_leads(
            [
                {"question_id": "q10", "option_label": "Sold a home"},
                {"question_id": "q5", "option_label": "Hand-me-downs"},
            ]
        )
    )
    await wm.initialize(
        session_id=session_id,
        person_id="p1",
        role_id="r1",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tribute_leads=leads_json,
    )
    state = await wm.get_state(session_id)
    assert pick_next_lead(state.tribute_leads).label == "q10"

    await wm.mark_tribute_lead_pursued(session_id, "q10")
    state2 = await wm.get_state(session_id)
    # q10 now pursued -> next un-pursued lead is q5.
    assert pick_next_lead(state2.tribute_leads).label == "q5"
