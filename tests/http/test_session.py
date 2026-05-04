"""``/session/start`` and ``/session/wrap`` route tests."""

from __future__ import annotations

from flashback.orchestrator import SessionStartResult
from flashback.orchestrator.stub import PersonNotFoundError
from flashback.working_memory.keys import (
    segment_key,
    state_key,
    transcript_key,
)
from tests.http.conftest import (
    FakeOrchestrator,
    auth_headers,
    new_uuids,
)


# --- /session/start --------------------------------------------------------


class TestSessionStart:
    async def test_happy_path(self, client, fake_redis, fake_orchestrator: FakeOrchestrator):
        session_id, person_id, role_id = new_uuids()
        fake_orchestrator.start_result = SessionStartResult(
            opener="Tell me about Maya.",
            phase="starter",
            selected_question_id=None,
        )

        resp = await client.post(
            "/session/start",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "session_metadata": {},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == session_id
        assert body["opener"] == "Tell me about Maya."
        assert body["metadata"]["phase"] == "starter"
        assert body["metadata"]["selected_question_id"] is None

        # WM populated.
        assert await fake_redis.exists(state_key(session_id))
        # Opener appended to transcript.
        items = await fake_redis.lrange(transcript_key(session_id), 0, -1)
        assert len(items) == 1
        assert b"Tell me about Maya." in items[0]

    async def test_person_not_found(self, client, fake_orchestrator: FakeOrchestrator):
        session_id, person_id, role_id = new_uuids()
        fake_orchestrator.start_raises = PersonNotFoundError(
            f"person {person_id} not found"
        )

        resp = await client.post(
            "/session/start",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "session_metadata": {},
            },
        )
        assert resp.status_code == 404
        assert person_id in resp.json()["detail"]

    async def test_seeds_rolling_summary_from_metadata(
        self, client, fake_redis, fake_orchestrator: FakeOrchestrator
    ):
        session_id, person_id, role_id = new_uuids()
        resp = await client.post(
            "/session/start",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "session_metadata": {"prior_session_summary": "We talked about Dad's garden."},
            },
        )
        assert resp.status_code == 200

        rolling = await fake_redis.hget(state_key(session_id), "rolling_summary")
        assert rolling == b"We talked about Dad's garden."

    async def test_selected_question_id_propagates(
        self, client, fake_redis, fake_orchestrator: FakeOrchestrator
    ):
        session_id, person_id, role_id = new_uuids()
        qid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        from uuid import UUID

        fake_orchestrator.start_result = SessionStartResult(
            opener="Tell me about Maya.",
            phase="starter",
            selected_question_id=UUID(qid),
        )
        resp = await client.post(
            "/session/start",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "session_metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["metadata"]["selected_question_id"] == qid

        # And the WM signal is set.
        seeded = await fake_redis.hget(state_key(session_id), "last_seeded_question_id")
        assert seeded == qid.encode()


# --- /session/wrap ---------------------------------------------------------


class TestSessionWrap:
    async def test_no_session_returns_409(self, client):
        session_id, person_id, _ = new_uuids()
        resp = await client.post(
            "/session/wrap",
            headers=auth_headers(),
            json={"session_id": session_id, "person_id": person_id},
        )
        assert resp.status_code == 409

    async def test_clears_working_memory(
        self, client, fake_redis, fake_orchestrator: FakeOrchestrator
    ):
        session_id, person_id, role_id = new_uuids()
        # Start the session first.
        await client.post(
            "/session/start",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "session_metadata": {},
            },
        )
        assert await fake_redis.exists(state_key(session_id))

        resp = await client.post(
            "/session/wrap",
            headers=auth_headers(),
            json={"session_id": session_id, "person_id": person_id},
        )
        assert resp.status_code == 200
        # All WM keys cleared.
        assert not await fake_redis.exists(state_key(session_id))
        assert not await fake_redis.exists(transcript_key(session_id))
        assert not await fake_redis.exists(segment_key(session_id))
