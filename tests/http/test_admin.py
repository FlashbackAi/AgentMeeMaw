"""``/admin/reset_phase`` route tests.

These exercise the canonical-graph write directly (not through the
orchestrator stub), so they require a real Postgres + pgvector test
database supplied via TEST_DATABASE_URL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers


async def _make_steady_person(pool, name: str = "Steady Subject") -> str:
    """Insert a person row already past the Handover Check."""
    coverage = '{"sensory":1,"voice":1,"place":1,"relation":1,"era":1}'
    locked_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO persons (name, phase, coverage_state, phase_locked_at)
                VALUES (%s, 'steady', %s::jsonb, %s)
                RETURNING id
                """,
                (name, coverage, locked_at),
            )
            (pid,) = await cur.fetchone()
        await conn.commit()
    return str(pid)


async def _read_person(pool, pid: str):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT phase, phase_locked_at, coverage_state
                FROM persons WHERE id = %s
                """,
                (pid,),
            )
            row = await cur.fetchone()
    return row


class TestResetPhase:
    async def test_flips_steady_back_to_starter(
        self, client_with_db, async_db_pool
    ):
        pid = await _make_steady_person(async_db_pool)

        resp = await client_with_db.post(
            "/admin/reset_phase",
            headers=auth_headers(),
            json={"person_id": pid},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["person_id"] == pid
        assert body["previous_phase"] == "steady"
        assert body["previous_locked_at"] is not None

        # Persisted state.
        row = await _read_person(async_db_pool, pid)
        assert row is not None
        phase, locked_at, coverage = row
        assert phase == "starter"
        assert locked_at is None
        assert coverage == {
            "sensory": 0,
            "voice": 0,
            "place": 0,
            "relation": 0,
            "era": 0,
        }

    async def test_404_for_unknown_person(self, client_with_db):
        bogus = str(uuid4())
        resp = await client_with_db.post(
            "/admin/reset_phase",
            headers=auth_headers(),
            json={"person_id": bogus},
        )
        assert resp.status_code == 404

    async def test_requires_service_token(self, client_with_db):
        pid = str(uuid4())
        resp = await client_with_db.post(
            "/admin/reset_phase",
            json={"person_id": pid},
        )
        assert resp.status_code == 401
