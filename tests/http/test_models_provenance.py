"""Contract tests for the user_id provenance field (spec D1/D2)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from flashback.http.models import SessionStartRequest, TurnRequest


def _session_body(**overrides):
    body = {"session_id": str(uuid4()), "person_id": str(uuid4())}
    body.update(overrides)
    return body


def _turn_body(**overrides):
    body = {
        "session_id": str(uuid4()),
        "person_id": str(uuid4()),
        "message": "hello",
    }
    body.update(overrides)
    return body


class TestSessionStartRequest:
    def test_accepts_user_id(self):
        uid = uuid4()
        req = SessionStartRequest(**_session_body(user_id=str(uid)))
        assert req.user_id == uid

    def test_user_id_defaults_to_none(self):
        req = SessionStartRequest(**_session_body())
        assert req.user_id is None

    def test_legacy_role_id_tolerated_and_ignored(self):
        # An un-updated Node still sends role_id; it must not 422 and
        # must not become provenance.
        req = SessionStartRequest(**_session_body(role_id=str(uuid4())))
        assert req.user_id is None

    def test_rejects_malformed_user_id(self):
        with pytest.raises(ValidationError):
            SessionStartRequest(**_session_body(user_id="not-a-uuid"))


class TestTurnRequest:
    def test_accepts_user_id(self):
        uid = uuid4()
        req = TurnRequest(**_turn_body(user_id=str(uid)))
        assert req.user_id == uid

    def test_user_id_defaults_to_none(self):
        req = TurnRequest(**_turn_body())
        assert req.user_id is None

    def test_legacy_role_id_tolerated_and_ignored(self):
        req = TurnRequest(**_turn_body(role_id=str(uuid4())))
        assert req.user_id is None
