from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from flashback.orchestrator.state import SessionStartState
from flashback.orchestrator.steps.apply_picked_question import apply_picked_question
from flashback.orchestrator.steps.starter_opener import build_starter_context
from flashback.phase_gate.schema import SelectionResult

PERSON_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ROLE_ID = UUID("99999999-9999-9999-9999-999999999999")
QID = UUID("33333333-3333-3333-3333-333333333333")


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def execute(self, sql, params=None):
        self._sql = sql

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Ctx(_Cursor(self._row))


class _Ctx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, row):
        self._row = row

    def connection(self):
        return _Ctx(_Conn(self._row))


class _Deps:
    def __init__(self, row):
        self.db_pool = _Pool(row)


def _state(metadata, phase="steady"):
    st = SessionStartState(
        session_id=SESSION_ID,
        person_id=PERSON_ID,
        role_id=ROLE_ID,
        session_metadata=metadata,
        started_at=datetime.now(timezone.utc),
        mode="text",
    )
    st.person_phase = phase
    return st


async def test_picked_question_sets_selection():
    row = (QID, "Tell me about the bike.", "dropped_reference")
    state = _state({"question_id": str(QID)})
    await apply_picked_question(state, _Deps(row))
    assert state.selection is not None
    assert state.selection.question_id == QID
    assert state.selection.question_text == "Tell me about the bike."
    assert state.selection.source == "dropped_reference"


async def test_picked_question_sets_selection_in_starter_phase():
    row = (QID, "Tell me about the bike.", "dropped_reference")
    state = _state({"question_id": str(QID)}, phase="starter")
    await apply_picked_question(state, _Deps(row))
    assert state.selection is not None
    assert state.selection.phase == "starter"
    assert state.selection.question_id == QID


async def test_no_question_id_is_noop():
    state = _state({})
    await apply_picked_question(state, _Deps(None))
    assert state.selection is None


async def test_unknown_question_id_degrades():
    state = _state({"question_id": str(QID)})
    await apply_picked_question(state, _Deps(None))  # fetchone -> None
    assert state.selection is None


async def test_explicit_pick_flag_set_on_starter_context():
    """A resolved feed pick flags the StarterContext as an explicit pick so
    the opener leads with the picked question."""
    row = (QID, "Tell me about the bike.", "dropped_reference")
    state = _state({"question_id": str(QID)})
    await apply_picked_question(state, _Deps(row))
    ctx = build_starter_context(state)
    assert ctx.anchor_is_explicit_pick is True
    assert ctx.anchor_question_text == "Tell me about the bike."


async def test_auto_selected_question_is_not_flagged_as_explicit_pick():
    """A starter question selected by the phase gate (no question_id in
    metadata) is not an explicit pick — the opener may bridge into it."""
    state = _state({})
    state.selection = SelectionResult(
        phase="starter",
        question_id=QID,
        question_text="Tell me about the bike.",
        source="dropped_reference",
        rationale="auto",
    )
    ctx = build_starter_context(state)
    assert ctx.anchor_is_explicit_pick is False
    assert ctx.anchor_question_text == "Tell me about the bike."


async def test_explicit_pick_suppresses_prior_session_summary_in_opener():
    """An explicit pick from an earlier session must not drag the opener
    toward the previous session's salient memory: continuity is withheld
    from the OPENER context (still seeded into Working Memory elsewhere)."""
    row = (QID, "Tell me about the electrical project.", "dropped_reference")
    state = _state(
        {"question_id": str(QID), "prior_session_summary": "Lots about the bus trip."}
    )
    await apply_picked_question(state, _Deps(row))
    ctx = build_starter_context(state)
    assert ctx.anchor_is_explicit_pick is True
    assert ctx.prior_session_summary is None


async def test_no_pick_keeps_prior_session_summary_in_opener():
    """Without an explicit pick, the returning-contributor opener keeps its
    continuity context."""
    state = _state({"prior_session_summary": "Lots about the bus trip."})
    ctx = build_starter_context(state)
    assert ctx.anchor_is_explicit_pick is False
    assert ctx.prior_session_summary == "Lots about the bus trip."
