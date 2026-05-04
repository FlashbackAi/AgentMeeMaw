from __future__ import annotations

from datetime import datetime, timezone

import fakeredis.aioredis
import httpx
import pytest_asyncio

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.intent_classifier.schema import IntentResult
from flashback.llm.errors import LLMError
from flashback.orchestrator import StubOrchestrator
from flashback.working_memory import WorkingMemory
from flashback.working_memory.keys import segment_key, transcript_key
from tests.http.conftest import auth_headers, new_uuids

SERVICE_TOKEN = "test-token"


@pytest_asyncio.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def app(fake_redis):
    cfg = HttpConfig(
        database_url="postgresql://unused-in-no-db-tests/x",
        valkey_url="redis://unused-in-tests/0",
        service_token=SERVICE_TOKEN,
        http_host="127.0.0.1",
        http_port=8000,
        working_memory_ttl_seconds=100,
        working_memory_transcript_limit=30,
        db_pool_min_size=1,
        db_pool_max_size=2,
    )
    application = create_app(cfg)
    application.state.redis = fake_redis
    application.state.db_pool = None
    application.state.working_memory = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class FixedClassifier:
    def __init__(self) -> None:
        self.calls = []

    async def classify(self, recent_turns, signals):
        self.calls.append({"recent_turns": recent_turns, "signals": signals})
        return IntentResult(
            intent="story",
            confidence="high",
            emotional_temperature="medium",
            reasoning="The latest turn is narrative.",
        )


class FailingClassifier:
    async def classify(self, recent_turns, signals):
        raise LLMError("classifier unavailable")


async def _init_wm(app, session_id, person_id, role_id):
    await app.state.working_memory.initialize(
        session_id=session_id,
        person_id=person_id,
        role_id=role_id,
        started_at=datetime.now(timezone.utc),
    )


async def test_turn_populates_intent_metadata_and_wm_signals(app, client, fake_redis):
    session_id, person_id, role_id = new_uuids()
    await _init_wm(app, session_id, person_id, role_id)
    classifier = FixedClassifier()
    app.state.orchestrator = StubOrchestrator(
        wm=app.state.working_memory,
        db_pool=None,
        intent_classifier=classifier,
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
    assert body["metadata"]["intent"] == "story"
    assert body["metadata"]["emotional_temperature"] == "medium"
    state = await app.state.working_memory.get_state(session_id)
    assert state.signal_last_intent == "story"
    assert state.signal_emotional_temperature_estimate == "medium"

    transcript = await fake_redis.lrange(transcript_key(session_id), 0, -1)
    segment = await fake_redis.lrange(segment_key(session_id), 0, -1)
    assert len(transcript) == 2
    assert len(segment) == 2
    assert b"She loved making pasta" in transcript[0]
    assert b"I hear you. Tell me more." in transcript[1]
    assert b"She loved making pasta" in segment[0]
    assert b"I hear you. Tell me more." in segment[1]
    assert classifier.calls


async def test_turn_degrades_when_intent_classifier_fails(app, client):
    session_id, person_id, role_id = new_uuids()
    await _init_wm(app, session_id, person_id, role_id)
    app.state.orchestrator = StubOrchestrator(
        wm=app.state.working_memory,
        db_pool=None,
        intent_classifier=FailingClassifier(),
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
    assert body["reply"] == "I hear you. Tell me more."
    assert body["metadata"]["intent"] is None
    assert body["metadata"]["emotional_temperature"] is None
    state = await app.state.working_memory.get_state(session_id)
    assert state.signal_last_intent == ""
    assert state.signal_emotional_temperature_estimate == ""
