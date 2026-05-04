from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from flashback.orchestrator.state import SessionWrapState


def test_session_wrap_state_defaults():
    state = SessionWrapState(
        session_id=uuid4(),
        person_id=uuid4(),
        started_at=datetime.now(timezone.utc),
    )

    assert state.final_segment_pushed is False
    assert state.session_summary_text == ""
    assert state.segments_pushed_count == 0
    assert state.trait_synthesizer_pushed is False
    assert state.profile_summary_pushed is False
    assert state.producers_per_session_pushed is False
    assert state.failures == {}
