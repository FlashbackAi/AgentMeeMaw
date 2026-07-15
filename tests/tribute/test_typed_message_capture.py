"""maybe_capture_typed_message — one-shot catch of a typed chat answer."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from flashback.tribute import message_capture
from flashback.tribute.message_capture import maybe_capture_typed_message

pytestmark = pytest.mark.anyio

_SESSION = UUID("00000000-0000-0000-0000-0000000000aa")
_PERSON = UUID("00000000-0000-0000-0000-0000000000bb")


class _State:
    def __init__(self, *, armed: bool, pending: bool, tribute_id="t1") -> None:
        self.signal_message_typed_check = "1" if armed else ""
        self.signal_pending_message = (
            json.dumps({"kind": "message", "text": "Say the thing."})
            if pending
            else ""
        )
        self.current_tribute_id = tribute_id


class _WM:
    def __init__(self, state: _State) -> None:
        self.state = state
        self.signal_updates: list[dict] = []
        self.cleared_pending = False

    async def get_state(self, session_id: str) -> _State:
        return self.state

    async def update_signals(self, session_id: str, **kw) -> None:
        self.signal_updates.append(kw)

    async def clear_pending_message(self, session_id: str) -> None:
        self.cleared_pending = True


async def test_captures_when_classifier_says_message(monkeypatch) -> None:
    stored: dict = {}

    async def fake_store(**kw):
        stored.update(kw)
        return kw["raw"]

    async def fake_classify(settings, *, invitation_copy, user_reply):
        assert invitation_copy == "Say the thing."
        return True

    monkeypatch.setattr(message_capture, "polish_and_store_message", fake_store)
    monkeypatch.setattr(
        "flashback.tribute.typed_message.is_direct_message", fake_classify
    )
    wm = _WM(_State(armed=True, pending=True))
    captured = await maybe_capture_typed_message(
        session_id=_SESSION, person_id=_PERSON,
        user_message="I never said it out loud, but you carried all of us.",
        wm=wm, db_pool=None, settings=None,
    )
    assert captured is True
    assert stored["source"] == "chat_typed"
    assert wm.cleared_pending is True
    # one-shot consumed
    assert {"signal_message_typed_check": ""} in wm.signal_updates


async def test_not_a_message_leaves_card_pending(monkeypatch) -> None:
    async def fake_classify(settings, *, invitation_copy, user_reply):
        return False

    async def must_not_store(**kw):  # pragma: no cover
        raise AssertionError("store must not run")

    monkeypatch.setattr(message_capture, "polish_and_store_message", must_not_store)
    monkeypatch.setattr(
        "flashback.tribute.typed_message.is_direct_message", fake_classify
    )
    wm = _WM(_State(armed=True, pending=True))
    captured = await maybe_capture_typed_message(
        session_id=_SESSION, person_id=_PERSON,
        user_message="haha that's a hard one, give me a second",
        wm=wm, db_pool=None, settings=None,
    )
    assert captured is False
    assert wm.cleared_pending is False          # card stays armed for the UI
    assert {"signal_message_typed_check": ""} in wm.signal_updates  # but one-shot spent


async def test_unarmed_never_runs_classifier(monkeypatch) -> None:
    async def must_not_classify(settings, **kw):  # pragma: no cover
        raise AssertionError("classifier must not run")

    monkeypatch.setattr(
        "flashback.tribute.typed_message.is_direct_message", must_not_classify
    )
    wm = _WM(_State(armed=False, pending=True))
    captured = await maybe_capture_typed_message(
        session_id=_SESSION, person_id=_PERSON,
        user_message="You were my hero.",
        wm=wm, db_pool=None, settings=None,
    )
    assert captured is False
    assert wm.signal_updates == []


async def test_classifier_failure_is_swallowed(monkeypatch) -> None:
    async def broken_classify(settings, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "flashback.tribute.typed_message.is_direct_message", broken_classify
    )
    wm = _WM(_State(armed=True, pending=True))
    captured = await maybe_capture_typed_message(
        session_id=_SESSION, person_id=_PERSON,
        user_message="You were my hero all along, and I never told you.",
        wm=wm, db_pool=None, settings=None,
    )
    assert captured is False
    assert wm.cleared_pending is False


async def test_too_short_reply_skipped(monkeypatch) -> None:
    async def must_not_classify(settings, **kw):  # pragma: no cover
        raise AssertionError("classifier must not run")

    monkeypatch.setattr(
        "flashback.tribute.typed_message.is_direct_message", must_not_classify
    )
    wm = _WM(_State(armed=True, pending=True))
    captured = await maybe_capture_typed_message(
        session_id=_SESSION, person_id=_PERSON,
        user_message="ok",
        wm=wm, db_pool=None, settings=None,
    )
    assert captured is False
