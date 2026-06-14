"""
Fake-cursor tests for told_by_user_id provenance on mark_processed.

No TEST_DATABASE_URL required — uses a fake cursor that captures SQL + params.

Spec (Task 7):
  - mark_processed INSERT INTO processed_extractions must include told_by_user_id.
  - Default told_by_user_id is None.
  - pg_notify payload must NOT include told_by_user_id (CLAUDE.md invariant #25).
"""

from __future__ import annotations

import json
from uuid import uuid4

from flashback.workers.extraction.idempotency import mark_processed


# ---------------------------------------------------------------------------
# Fake cursor
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Synchronous fake cursor that records (sql, params) for each execute().

    fetchone() returns a truthy row for INSERT ... RETURNING (so the inserted=True
    path fires) and None otherwise.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._row_id: str = str(uuid4())

    def execute(self, sql: str, params=None) -> None:
        self.calls.append((sql, params or ()))

    def fetchone(self):
        last_sql, _ = self.calls[-1]
        if "RETURNING" in last_sql:
            return (self._row_id,)
        return None

    def insert_sqls(self) -> list[tuple[str, tuple]]:
        """Return only the INSERT statements captured."""
        return [(sql, params) for sql, params in self.calls if "INSERT" in sql]

    def notify_params(self) -> list[tuple]:
        """Return params tuples for pg_notify calls."""
        return [
            params
            for sql, params in self.calls
            if "pg_notify" in sql
        ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MESSAGE_ID = f"msg-{uuid4()}"
_PERSON_ID = str(uuid4())
_SESSION_ID = str(uuid4())
_USER_ID = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mark_processed_records_told_by_user_id():
    """INSERT INTO processed_extractions includes told_by_user_id column + value."""
    cur = _FakeCursor()
    mark_processed(
        cur,
        message_id=_MESSAGE_ID,
        person_id=_PERSON_ID,
        session_id=_SESSION_ID,
        moments_written=1,
        told_by_user_id=_USER_ID,
    )
    inserts = cur.insert_sqls()
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "told_by_user_id" in sql
    assert _USER_ID in params


def test_mark_processed_told_by_defaults_none():
    """Omitting told_by_user_id sends None as the INSERT param."""
    cur = _FakeCursor()
    mark_processed(
        cur,
        message_id=_MESSAGE_ID,
        person_id=_PERSON_ID,
        session_id=_SESSION_ID,
        moments_written=0,
    )
    inserts = cur.insert_sqls()
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "told_by_user_id" in sql
    assert None in params


def test_mark_processed_notify_payload_excludes_told_by():
    """pg_notify JSON payload must NOT contain 'told_by_user_id' (invariant #25)."""
    cur = _FakeCursor()
    mark_processed(
        cur,
        message_id=_MESSAGE_ID,
        person_id=_PERSON_ID,
        session_id=_SESSION_ID,
        moments_written=2,
        told_by_user_id=_USER_ID,
    )
    notify_calls = cur.notify_params()
    assert len(notify_calls) == 1, "Expected exactly one pg_notify call"
    # The second param to pg_notify is the JSON payload string
    channel, payload_str = notify_calls[0]
    payload = json.loads(payload_str)
    assert "told_by_user_id" not in payload
