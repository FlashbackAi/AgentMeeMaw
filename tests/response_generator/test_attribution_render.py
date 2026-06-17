"""Cross-contributor attribution rendering (sub-project 2)."""

from datetime import datetime, timezone
from uuid import uuid4

from flashback.response_generator.context import render_turn_context
from flashback.response_generator.schema import TurnContext
from flashback.retrieval.schema import MomentResult


def _moment(told_by_user_id=None, told_by_display_name=None, title="Halwa lessons", told_by_relationship=None):
    return MomentResult(
        id=uuid4(),
        person_id=uuid4(),
        title=title,
        narrative="She taught me to make halwa.",
        time_anchor=None,
        life_period_estimate=None,
        sensory_details=None,
        emotional_tone=None,
        contributor_perspective=None,
        created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        told_by_user_id=told_by_user_id,
        told_by_display_name=told_by_display_name,
        told_by_relationship=told_by_relationship,
    )


def _ctx(current_user_id, moments):
    return TurnContext(
        person_name="Lakshmi",
        intent="recall",
        emotional_temperature="medium",
        current_user_id=current_user_id,
        related_moments=moments,
    )


def test_turn_context_accepts_current_user_id():
    uid = uuid4()
    assert _ctx(uid, []).current_user_id == uid


def test_turn_context_current_user_id_defaults_none():
    ctx = TurnContext(person_name="L", intent="recall", emotional_temperature="medium")
    assert ctx.current_user_id is None


def test_other_contributor_moment_is_attributed():
    me, other = uuid4(), uuid4()
    rendered = render_turn_context(
        _ctx(me, [_moment(told_by_user_id=other, told_by_display_name="Ravi")])
    )
    assert 'told_by="Ravi"' in rendered


def test_own_moment_not_attributed():
    me = uuid4()
    rendered = render_turn_context(
        _ctx(me, [_moment(told_by_user_id=me, told_by_display_name="Priya")])
    )
    assert "told_by=" not in rendered


def test_null_provenance_moment_not_attributed():
    me = uuid4()
    rendered = render_turn_context(_ctx(me, [_moment()]))
    assert "told_by=" not in rendered


def test_no_current_user_no_attribution():
    other = uuid4()
    rendered = render_turn_context(
        _ctx(None, [_moment(told_by_user_id=other, told_by_display_name="Ravi")])
    )
    assert "told_by=" not in rendered


def test_single_contributor_render_is_unattributed():
    """All moments own-or-null + a known speaker -> zero labels (spec D4 no-op)."""
    me = uuid4()
    rendered = render_turn_context(
        _ctx(me, [
            _moment(told_by_user_id=me, told_by_display_name="Priya", title="A"),
            _moment(title="B"),  # creator-era null
        ])
    )
    assert "told_by=" not in rendered


def test_cross_contributor_moment_renders_relationship():
    me, other = uuid4(), uuid4()
    m = _moment(told_by_user_id=other, told_by_display_name="Ravi", told_by_relationship="her brother")
    rendered = render_turn_context(_ctx(me, [m]))
    assert 'told_by="Ravi"' in rendered
    assert 'relationship="her brother"' in rendered


def test_recall_prompt_has_relationship_instruction():
    from flashback.response_generator.prompts import RECALL_PROMPT
    assert "relationship=" in RECALL_PROMPT
