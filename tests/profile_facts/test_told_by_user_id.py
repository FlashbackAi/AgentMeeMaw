"""Unit tests for told_by_user_id provenance stamping in profile_facts.

These tests use fake cursors (no DB) to verify the INSERT_FACT params dict
includes told_by_user_id when supplied and defaults to None when omitted.
They mirror the upsert_fact function's internal logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flashback.profile_facts.repository import upsert_fact, upsert_fact_async
from flashback.profile_facts.schema import FactUpsertRequest


# ---------------------------------------------------------------------------
# Fake cursor helpers
# ---------------------------------------------------------------------------


def _noop_embedding(**kwargs):
    """Push-embedding stub that does nothing."""


class FakeSyncCursor:
    """Fake psycopg synchronous cursor.

    Simulates: SELECT_ACTIVE_FACT_BY_KEY returns None (new key),
    then COUNT_ACTIVE_FACTS returns 0 (cap not reached),
    then INSERT_FACT.
    We capture the params passed to INSERT_FACT.
    """

    def __init__(self):
        # sequence: (None for SELECT_ACTIVE_FACT_BY_KEY, (0,) for COUNT)
        self._fetchone_seq = iter([None, (0,)])
        self.last_insert_params = None
        self._call_count = 0

    def execute(self, sql, params=None):
        self._call_count += 1
        # 3rd execute is the INSERT_FACT (SELECT, COUNT, INSERT)
        if "INSERT INTO profile_facts" in sql:
            self.last_insert_params = dict(params)

    def fetchone(self):
        return next(self._fetchone_seq, None)


class FakeAsyncCursor:
    """Fake psycopg async cursor (awaitable execute/fetchone)."""

    def __init__(self):
        self._fetchone_seq = iter([None, (0,)])
        self.last_insert_params = None

    async def execute(self, sql, params=None):
        if "INSERT INTO profile_facts" in sql:
            self.last_insert_params = dict(params)

    async def fetchone(self):
        return next(self._fetchone_seq, None)


# ---------------------------------------------------------------------------
# Sync upsert_fact tests
# ---------------------------------------------------------------------------


def test_upsert_fact_stamps_told_by():
    """told_by_user_id kwarg flows into the INSERT_FACT params dict."""
    cur = FakeSyncCursor()
    user_id = str(uuid4())

    upsert_fact(
        cur,
        person_id=str(uuid4()),
        fact_key="birthplace",
        question_text="Where was she born?",
        answer_text="Mumbai",
        source="starter_extraction",
        push_embedding=_noop_embedding,
        embedding_model="voyage-3",
        embedding_model_version="1",
        told_by_user_id=user_id,
    )

    assert cur.last_insert_params is not None, "INSERT was never called"
    assert cur.last_insert_params["told_by_user_id"] == user_id


def test_upsert_fact_null_without_user():
    """Omitting told_by_user_id leaves None in the params dict."""
    cur = FakeSyncCursor()

    upsert_fact(
        cur,
        person_id=str(uuid4()),
        fact_key="birthplace",
        question_text="Where was she born?",
        answer_text="Mumbai",
        source="starter_extraction",
        push_embedding=_noop_embedding,
        embedding_model="voyage-3",
        embedding_model_version="1",
    )

    assert cur.last_insert_params is not None, "INSERT was never called"
    assert cur.last_insert_params["told_by_user_id"] is None


# ---------------------------------------------------------------------------
# Async upsert_fact_async tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_fact_async_stamps_told_by():
    """told_by_user_id flows into upsert_fact_async INSERT_FACT params."""
    cur = FakeAsyncCursor()
    user_id = str(uuid4())

    await upsert_fact_async(
        cur,
        person_id=str(uuid4()),
        fact_key="residence",
        question_text="Where does she live?",
        answer_text="Bangalore",
        source="user_edit",
        push_embedding=_noop_embedding,
        embedding_model="voyage-3",
        embedding_model_version="1",
        told_by_user_id=user_id,
    )

    assert cur.last_insert_params is not None, "INSERT was never called"
    assert cur.last_insert_params["told_by_user_id"] == user_id


@pytest.mark.asyncio
async def test_upsert_fact_async_null_without_user():
    """Omitting told_by_user_id in async variant leaves None."""
    cur = FakeAsyncCursor()

    await upsert_fact_async(
        cur,
        person_id=str(uuid4()),
        fact_key="residence",
        question_text="Where does she live?",
        answer_text="Bangalore",
        source="user_edit",
        push_embedding=_noop_embedding,
        embedding_model="voyage-3",
        embedding_model_version="1",
    )

    assert cur.last_insert_params is not None, "INSERT was never called"
    assert cur.last_insert_params["told_by_user_id"] is None


# ---------------------------------------------------------------------------
# Schema: FactUpsertRequest accepts user_id
# ---------------------------------------------------------------------------


def test_upsert_request_accepts_user_id():
    """FactUpsertRequest accepts an optional user_id (UUID), defaults None."""
    uid = uuid4()
    req = FactUpsertRequest(
        person_id=uuid4(),
        fact_key="birthplace",
        answer_text="Mumbai",
        user_id=uid,
    )
    assert req.user_id == uid


def test_upsert_request_user_id_defaults_none():
    req = FactUpsertRequest(
        person_id=uuid4(),
        fact_key="birthplace",
        answer_text="Mumbai",
    )
    assert req.user_id is None
