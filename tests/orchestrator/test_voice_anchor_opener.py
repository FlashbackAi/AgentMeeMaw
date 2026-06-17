"""Voice anchor flows into the StarterContext + rendered opener (sub-project 3)."""

from datetime import datetime, timezone
from uuid import uuid4

from flashback.orchestrator.steps.starter_opener import build_starter_context
from flashback.orchestrator.state import SessionStartState
from flashback.response_generator.context import render_starter_context


def _state(meta):
    s = SessionStartState(
        session_id=uuid4(),
        person_id=uuid4(),
        user_id=uuid4(),
        session_metadata=meta,
        started_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
    )
    s.person_name = "LegacyTest1"
    s.person_relationship = None
    s.person_gender = "he"
    return s


def test_voice_anchor_surfaces_in_starter_context_and_render():
    state = _state({"contributor_voice_anchor": "his daughter"})
    ctx = build_starter_context(state)
    assert ctx.contributor_voice_anchor == "his daughter"
    rendered = render_starter_context(ctx)
    assert "his daughter" in rendered


def test_no_voice_anchor_is_none():
    ctx = build_starter_context(_state({}))
    assert ctx.contributor_voice_anchor is None
