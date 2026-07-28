"""Pure-fake tests for the select_collaborator_onboarding_tap step."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

import importlib
import sys

# Import the submodule directly (the steps __init__ re-exports the function
# under the same dotted name, so we must grab the actual module object).
import flashback.orchestrator.steps.select_collaborator_onboarding_tap  # noqa: F401
_mod = sys.modules["flashback.orchestrator.steps.select_collaborator_onboarding_tap"]

from flashback.collaborator_onboarding.repository import OnboardingState
from flashback.orchestrator.state import TurnState

pytestmark = pytest.mark.asyncio

PERSON_ID = uuid4()
USER_ID = uuid4()
SESSION_ID = uuid4()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _WMState:
    """Minimal stand-in for WorkingMemoryState."""

    def __init__(self, *, collaborator_onboarding_tap_emitted: bool = False) -> None:
        self.collaborator_onboarding_tap_emitted = collaborator_onboarding_tap_emitted


class _FakeWM:
    """Fake working memory that records calls."""

    def __init__(self, *, wm_state: _WMState | None = None) -> None:
        self._state = wm_state or _WMState()
        self.record_tap_calls: list[dict] = []
        self.update_signals_calls: list[dict] = []

    async def get_state(self, session_id: str) -> _WMState:
        return self._state

    async def record_tap_emitted(
        self,
        *,
        session_id: str,
        question_id: str,
        question_text: str = "",
    ) -> None:
        self.record_tap_calls.append(
            {"session_id": session_id, "question_id": question_id, "question_text": question_text}
        )

    async def update_signals(self, session_id: str, **kw) -> None:
        self.update_signals_calls.append({"session_id": session_id, **kw})


class _FakeConn:
    async def commit(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def connection(self):
        return _FakeConn()


class _FakeDeps:
    def __init__(self, *, wm: _FakeWM) -> None:
        self.db_pool = _FakePool()
        self.working_memory = wm
        self.settings = object()


def _make_state(
    *,
    user_id: UUID | None = USER_ID,
    wm_state: _WMState | None = None,
) -> TurnState:
    s = TurnState(
        turn_id=uuid4(),
        session_id=SESSION_ID,
        person_id=PERSON_ID,
        user_id=user_id,
        user_message="Tell me more",
        started_at=datetime.now(timezone.utc),
    )
    s.working_memory_state = wm_state
    s.person_relationship = "his daughter"
    return s


# ---------------------------------------------------------------------------
# Helpers to install deterministic monkeypatches
# ---------------------------------------------------------------------------


def _patch_all(
    monkeypatch,
    *,
    onboarding_state: OnboardingState | None,
    voice_anchor: str | None = "his daughter",
    tap_text: str = "Share a memory of David",
    tap_options: list[str] | None = None,
    name: str = "David",
) -> None:
    """Patch the module-level callables used by the step."""

    async def _fake_get_onboarding_state(conn, *, person_id, user_id):
        return onboarding_state

    async def _fake_get_voice_anchor(conn, *, person_id, user_id):
        return voice_anchor

    async def _fake_increment_taps_emitted(conn, *, person_id, user_id):
        pass

    async def _fake_generate_onboarding_tap(
        *, settings, person_name, relationship, **kwargs
    ):
        return (tap_text, tap_options or [])

    async def _fake_read_name(deps, person_id):
        return (name, None)

    monkeypatch.setattr(_mod, "get_onboarding_state", _fake_get_onboarding_state)
    monkeypatch.setattr(_mod, "get_voice_anchor", _fake_get_voice_anchor)
    monkeypatch.setattr(_mod, "increment_taps_emitted", _fake_increment_taps_emitted)
    monkeypatch.setattr(_mod, "generate_onboarding_tap", _fake_generate_onboarding_tap)
    monkeypatch.setattr(_mod, "_read_name", _fake_read_name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_emits_tap_for_onboarding_collaborator(monkeypatch):
    """Step emits one tap and sets WM flag when phase=onboarding, has_memory=False."""
    st = OnboardingState(phase="onboarding", has_memory=False, has_connection=False, taps_emitted=0)
    wm = _FakeWM(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    _patch_all(monkeypatch, onboarding_state=st)

    state = _make_state(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    deps = _FakeDeps(wm=wm)

    await _mod.select_collaborator_onboarding_tap(state, deps)

    assert len(state.taps) == 1
    assert state.taps[0].text == "Share a memory of David"
    assert state.taps[0].dimension == "onboarding"
    assert wm.update_signals_calls
    assert wm.update_signals_calls[0].get("collaborator_onboarding_tap_emitted") is True


async def test_noop_when_phase_active(monkeypatch):
    """No tap emitted when the collaborator's phase is already 'active'."""
    st = OnboardingState(phase="active", has_memory=True, has_connection=False, taps_emitted=1)
    wm = _FakeWM(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    _patch_all(monkeypatch, onboarding_state=st)

    state = _make_state(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    deps = _FakeDeps(wm=wm)

    await _mod.select_collaborator_onboarding_tap(state, deps)

    assert state.taps == []
    assert wm.update_signals_calls == []


async def test_noop_when_wm_flag_already_true(monkeypatch):
    """No tap emitted when collaborator_onboarding_tap_emitted is True in WM."""
    st = OnboardingState(phase="onboarding", has_memory=False, has_connection=False, taps_emitted=1)
    wm_state = _WMState(collaborator_onboarding_tap_emitted=True)
    wm = _FakeWM(wm_state=wm_state)
    _patch_all(monkeypatch, onboarding_state=st)

    state = _make_state(wm_state=wm_state)
    deps = _FakeDeps(wm=wm)

    await _mod.select_collaborator_onboarding_tap(state, deps)

    assert state.taps == []
    assert wm.update_signals_calls == []


async def test_noop_when_user_id_is_none(monkeypatch):
    """No tap emitted when user_id is None (creator-era / no collaborator)."""
    wm = _FakeWM(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    _patch_all(
        monkeypatch,
        onboarding_state=OnboardingState(
            phase="onboarding", has_memory=False, has_connection=False, taps_emitted=0
        ),
    )

    state = _make_state(user_id=None, wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    deps = _FakeDeps(wm=wm)

    await _mod.select_collaborator_onboarding_tap(state, deps)

    assert state.taps == []
    assert wm.update_signals_calls == []


async def test_noop_when_has_memory_but_still_onboarding(monkeypatch):
    """No tap when has_memory=True even though phase hasn't flipped yet.

    Represents the brief window where a moment has been extracted (has_memory=True,
    has_connection=True) but the async phase flip to 'active' hasn't run. The step
    must not re-emit a tap once the memory is present.
    """
    st = OnboardingState(phase="onboarding", has_memory=True, has_connection=True, taps_emitted=0)
    wm = _FakeWM(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    _patch_all(monkeypatch, onboarding_state=st)

    state = _make_state(wm_state=_WMState(collaborator_onboarding_tap_emitted=False))
    deps = _FakeDeps(wm=wm)

    await _mod.select_collaborator_onboarding_tap(state, deps)

    assert state.taps == []
    assert wm.update_signals_calls == []
