"""``/turn`` route tests."""

from __future__ import annotations

from uuid import UUID

from flashback.orchestrator import Tap
from flashback.orchestrator import TurnResult
from flashback.working_memory.keys import segment_key, transcript_key
from tests.http.conftest import (
    FakeOrchestrator,
    auth_headers,
    new_uuids,
)


async def _start_session(client, session_id, person_id, role_id):
    return await client.post(
        "/session/start",
        headers=auth_headers(),
        json={
            "session_id": session_id,
            "person_id": person_id,
            "role_id": role_id,
            "session_metadata": {},
        },
    )


class TestTurn:
    async def test_happy_path(self, client, fake_redis, fake_orchestrator: FakeOrchestrator):
        session_id, person_id, role_id = new_uuids()
        await _start_session(client, session_id, person_id, role_id)

        fake_orchestrator.turn_result = TurnResult(
            reply="That sounds wonderful.",
            intent="story",
            emotional_temperature="medium",
            segment_boundary=False,
            taps=[],
        )

        resp = await client.post(
            "/turn",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "She loved making pasta from scratch.",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "That sounds wonderful."
        assert body["metadata"]["intent"] == "story"
        assert body["metadata"]["emotional_temperature"] == "medium"
        assert body["metadata"]["segment_boundary"] is False
        assert body["metadata"]["taps"] == []

        # WM has 3 entries: the opener, the user message, the assistant reply.
        items = await fake_redis.lrange(transcript_key(session_id), 0, -1)
        assert len(items) == 3
        assert b"opener" not in items[0]  # opener content varies
        assert b"She loved making pasta" in items[1]
        assert b"That sounds wonderful" in items[2]

    async def test_taps_shape(self, client, fake_orchestrator: FakeOrchestrator):
        session_id, person_id, role_id = new_uuids()
        await _start_session(client, session_id, person_id, role_id)
        qid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        fake_orchestrator.turn_result = TurnResult(
            reply="We can go there.",
            intent="switch",
            emotional_temperature="medium",
            segment_boundary=False,
            taps=[Tap(question_id=qid, text="What was her work like?", dimension="era")],
        )

        resp = await client.post(
            "/turn",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "Let's switch.",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["metadata"]["taps"] == [
            {
                "question_id": str(qid),
                "text": "What was her work like?",
                "dimension": "era",
            }
        ]

    async def test_unstarted_session_returns_409(self, client):
        session_id, person_id, role_id = new_uuids()
        resp = await client.post(
            "/turn",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "hello",
            },
        )
        assert resp.status_code == 409

    async def test_transcript_truncates_after_30_turns(
        self, client, fake_redis, fake_orchestrator: FakeOrchestrator
    ):
        session_id, person_id, role_id = new_uuids()
        await _start_session(client, session_id, person_id, role_id)

        # /session/start already added 1 (opener). Each /turn call adds 2
        # (user + assistant). 17 turns -> 1 + 17*2 = 35 entries; transcript
        # caps at 30.
        for i in range(17):
            resp = await client.post(
                "/turn",
                headers=auth_headers(),
                json={
                    "session_id": session_id,
                    "person_id": person_id,
                    "role_id": role_id,
                    "message": f"msg-{i}",
                },
            )
            assert resp.status_code == 200

        items = await fake_redis.lrange(transcript_key(session_id), 0, -1)
        assert len(items) == 30
        # Segment is not trimmed.
        seg = await fake_redis.lrange(segment_key(session_id), 0, -1)
        assert len(seg) == 35

    async def test_empty_message_rejected(self, client):
        session_id, person_id, role_id = new_uuids()
        resp = await client.post(
            "/turn",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "",
            },
        )
        # Pydantic validation -> 422.
        assert resp.status_code == 422
