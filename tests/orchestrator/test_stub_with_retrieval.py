from __future__ import annotations

from datetime import datetime, timezone

import fakeredis.aioredis
import httpx
import pytest_asyncio

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.intent_classifier.schema import IntentResult
from flashback.orchestrator import StubOrchestrator
from flashback.working_memory import WorkingMemory
from tests.http.conftest import auth_headers, new_uuids

SERVICE_TOKEN = "test-token"


class FixedClassifier:
    def __init__(self, intent: str) -> None:
        self.intent = intent

    async def classify(self, recent_turns, signals):
        return IntentResult(
            intent=self.intent,
            confidence="high",
            emotional_temperature="low",
            reasoning="test-controlled",
        )


class FakeRetrieval:
    def __init__(self, *, raise_on_search: bool = False) -> None:
        self.raise_on_search = raise_on_search
        self.search_calls = []
        self.entity_calls = []
        self.thread_calls = []

    async def search_moments(self, *, query, person_id):
        self.search_calls.append({"query": query, "person_id": person_id})
        if self.raise_on_search:
            raise RuntimeError("db unavailable")
        return [object()]

    async def get_entities(self, person_id):
        self.entity_calls.append(person_id)
        return [object()]

    async def get_threads(self, person_id):
        self.thread_calls.append(person_id)
        return [object()]


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


async def _init_wm(app, session_id, person_id, role_id):
    await app.state.working_memory.initialize(
        session_id=session_id,
        person_id=person_id,
        role_id=role_id,
        started_at=datetime.now(timezone.utc),
    )


async def _post_turn(app, client, *, classifier, retrieval):
    session_id, person_id, role_id = new_uuids()
    await _init_wm(app, session_id, person_id, role_id)
    app.state.orchestrator = StubOrchestrator(
        wm=app.state.working_memory,
        db_pool=None,
        intent_classifier=classifier,
        retrieval=retrieval,
    )

    response = await client.post(
        "/turn",
        headers=auth_headers(),
        json={
            "session_id": session_id,
            "person_id": person_id,
            "role_id": role_id,
            "message": "Do you remember the porch?",
        },
    )
    return response


async def test_recall_intent_calls_retrieval(app, client):
    retrieval = FakeRetrieval()

    response = await _post_turn(
        app,
        client,
        classifier=FixedClassifier("recall"),
        retrieval=retrieval,
    )

    assert response.status_code == 200
    assert len(retrieval.search_calls) == 1
    assert retrieval.entity_calls == []
    assert retrieval.thread_calls == []


async def test_deepen_intent_skips_retrieval(app, client):
    retrieval = FakeRetrieval()

    response = await _post_turn(
        app,
        client,
        classifier=FixedClassifier("deepen"),
        retrieval=retrieval,
    )

    assert response.status_code == 200
    assert retrieval.search_calls == []


async def test_retrieval_failure_still_returns_200(app, client):
    retrieval = FakeRetrieval(raise_on_search=True)

    response = await _post_turn(
        app,
        client,
        classifier=FixedClassifier("recall"),
        retrieval=retrieval,
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "I hear you. Tell me more."


async def test_switch_intent_fetches_broader_context(app, client):
    retrieval = FakeRetrieval()

    response = await _post_turn(
        app,
        client,
        classifier=FixedClassifier("switch"),
        retrieval=retrieval,
    )

    assert response.status_code == 200
    assert len(retrieval.search_calls) == 1
    assert len(retrieval.entity_calls) == 1
    assert len(retrieval.thread_calls) == 1
