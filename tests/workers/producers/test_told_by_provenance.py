"""Tests: told_by_user_id provenance stamps for per-session producers."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from flashback.workers.producers.persistence import persist_producer_result
from flashback.workers.producers.schema import GeneratedQuestion, ProducerMessage, ProducerResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(person_id: str | None = None) -> ProducerResult:
    return ProducerResult(
        person_id=UUID(person_id or str(uuid4())),
        source_tag="life_period_gap",
        overall_reasoning="test",
        questions=[
            GeneratedQuestion(
                text="What was it like?",
                themes=["era"],
                attributes={"life_period": "1970s"},
            )
        ],
    )


class FakeCursor:
    """Minimal fake cursor that records the last execute() call."""

    def __init__(self) -> None:
        self._last_sql: str = ""
        self._last_params: tuple = ()
        self._return_id = str(uuid4())

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._last_sql = sql
        self._last_params = params

    def fetchone(self):
        return (self._return_id,)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_producer_message_parses_told_by_user_id():
    uid = str(uuid4())
    msg = ProducerMessage.model_validate(
        {
            "person_id": str(uuid4()),
            "producer": "P2",
            "session_id": str(uuid4()),
            "told_by_user_id": uid,
        }
    )
    assert str(msg.told_by_user_id) == uid


def test_producer_message_told_by_defaults_none():
    msg = ProducerMessage.model_validate(
        {"person_id": str(uuid4()), "producer": "P2"}
    )
    assert msg.told_by_user_id is None


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------

def test_insert_question_stamps_told_by():
    """persist_producer_result with told_by_user_id includes it in the INSERT."""
    cursor = FakeCursor()
    uid = str(uuid4())
    result = _make_result()

    persist_producer_result(cursor, result=result, told_by_user_id=uid)

    sql = cursor._last_sql
    params = cursor._last_params

    # Column list must include told_by_user_id
    assert "told_by_user_id" in sql
    # Params must include the uid (last positional param)
    assert uid in params
    # Exactly 5 %s placeholders
    assert sql.count("%s") == 5
    # Exactly 5 params
    assert len(params) == 5


def test_insert_question_null_without_session_user():
    """persist_producer_result with no told_by_user_id passes None in the INSERT."""
    cursor = FakeCursor()
    result = _make_result()

    persist_producer_result(cursor, result=result)

    params = cursor._last_params
    # 5 params, last one is None
    assert len(params) == 5
    assert params[-1] is None
