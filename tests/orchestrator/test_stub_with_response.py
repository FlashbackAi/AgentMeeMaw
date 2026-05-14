from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import fakeredis.aioredis
import httpx
import pytest_asyncio

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.intent_classifier.schema import IntentResult
from flashback.llm.errors import LLMError
from flashback.orchestrator import Orchestrator
from flashback.response_generator import generator as generator_module
from flashback.response_generator.generator import ResponseGenerator
from flashback.response_generator.prompts import STORY_PROMPT
from flashback.retrieval.schema import MomentResult
from flashback.working_memory import WorkingMemory
from tests.http.conftest import auth_headers, new_uuids

SERVICE_TOKEN = "test-token"
SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")
QUESTION_ID = UUID("55555555-5555-5555-5555-555555555555")


class FixedClassifier:
    def __init__(self, intent: str = "story") -> None:
        self.intent = intent

    async def classify(self, recent_turns, signals):
        return IntentResult(
            intent=self.intent,
            confidence="high",
            emotional_temperature="medium",
            reasoning="test-controlled",
        )


class FailingClassifier:
    async def classify(self, recent_turns, signals):
        raise LLMError("classifier unavailable")


class FakeRetrieval:
    def __init__(self) -> None:
        self.moment = MomentResult(
            id=UUID("22222222-2222-2222-2222-222222222222"),
            person_id=UUID("11111111-1111-1111-1111-111111111111"),
            title="Porch evenings",
            narrative="Maya sat on the porch after dinner.",
            time_anchor=None,
            life_period_estimate=None,
            sensory_details="warm light",
            emotional_tone="tender",
            contributor_perspective="adult child",
            created_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
            similarity_score=0.32,
        )

    async def search_moments(self, *, query, person_id):
        self.moment.person_id = person_id
        return [self.moment]

    async def search_entities(self, *, query, person_id):
        return []

    async def get_entities(self, person_id):
        return []

    async def get_threads(self, person_id):
        return []


class FakeDbPool:
    def __init__(self, person_name: str = "Maya") -> None:
        self.person_name = person_name
        self.relationship = "mother"
        self.phase = "starter"
        self.question_text = "What's a smell that brings them right back?"
        self.question_dimension = "sensory"

    def connection(self):
        return _AsyncContext(FakeConnection(self))


class FakeConnection:
    def __init__(self, pool: FakeDbPool) -> None:
        self.pool = pool

    def cursor(self):
        return _AsyncContext(FakeCursor(self.pool))


class FakeCursor:
    def __init__(self, pool: FakeDbPool) -> None:
        self.pool = pool
        self.sql = ""

    async def execute(self, sql, params=None):
        self.sql = sql

    async def fetchone(self):
        if "FROM persons" in self.sql:
            return (self.pool.person_name, self.pool.relationship, self.pool.phase)
        if "FROM active_moments" in self.sql:
            return (False,)
        if "FROM active_questions" in self.sql:
            return (QUESTION_ID, self.pool.question_text)
        raise AssertionError(f"unexpected SQL: {self.sql}")

    async def fetchall(self):
        return []


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest_asyncio.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


def _config() -> HttpConfig:
    return HttpConfig(
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


def _response_generator() -> ResponseGenerator:
    return ResponseGenerator(
        settings=SETTINGS,
        provider="anthropic",
        model="claude-sonnet-4-6",
        timeout=12,
        max_tokens=400,
    )


async def _app(fake_redis, *, classifier, retrieval=None):
    cfg = _config()
    application = create_app(cfg)
    application.state.redis = fake_redis
    application.state.db_pool = FakeDbPool()
    application.state.working_memory = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    application.state.orchestrator = Orchestrator(
        wm=application.state.working_memory,
        db_pool=application.state.db_pool,
        intent_classifier=classifier,
        retrieval=retrieval,
        response_generator=_response_generator(),
    )
    return application


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _init_wm(app, session_id, person_id, role_id):
    await app.state.working_memory.initialize(
        session_id=session_id,
        person_id=person_id,
        role_id=role_id,
        started_at=datetime.now(timezone.utc),
    )


async def test_session_start_returns_generated_opener_with_person_name(
    fake_redis, monkeypatch
):
    monkeypatch.setattr(
        generator_module,
        "call_text",
        AsyncMock(return_value="Flashback here. When you think of Maya, what smell brings her right back?"),
    )
    app = await _app(fake_redis, classifier=FixedClassifier())
    async with await _client(app) as client:
        session_id, person_id, role_id = new_uuids()
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
    assert "Maya" in body["opener"]
    assert body["metadata"]["selected_question_id"] is None
    assert body["metadata"]["taps"] == []


async def test_turn_recall_passes_retrieval_results_to_response_prompt(
    fake_redis, monkeypatch
):
    call = AsyncMock(return_value="That porch detail has stayed vivid.")
    monkeypatch.setattr(generator_module, "call_text", call)
    app = await _app(
        fake_redis,
        classifier=FixedClassifier("recall"),
        retrieval=FakeRetrieval(),
    )
    session_id, person_id, role_id = new_uuids()
    await _init_wm(app, session_id, person_id, role_id)

    async with await _client(app) as client:
        resp = await client.post(
            "/turn",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "Do you remember the porch?",
            },
        )

    assert resp.status_code == 200
    user_message = call.await_args.kwargs["user_message"]
    assert "Porch evenings" in user_message
    assert "Maya sat on the porch after dinner." in user_message


async def test_turn_response_generation_failure_maps_to_503(fake_redis, monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "call_text",
        AsyncMock(side_effect=LLMError("response unavailable")),
    )
    app = await _app(fake_redis, classifier=FixedClassifier("story"))
    session_id, person_id, role_id = new_uuids()
    await _init_wm(app, session_id, person_id, role_id)

    async with await _client(app) as client:
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

    assert resp.status_code == 503
    assert resp.json() == {
        "error": "service_unavailable",
        "detail": "response generation failed",
    }


async def test_classifier_failure_defaults_generator_to_story_prompt(
    fake_redis, monkeypatch
):
    call = AsyncMock(return_value="That is a warm detail.")
    monkeypatch.setattr(generator_module, "call_text", call)
    app = await _app(fake_redis, classifier=FailingClassifier())
    session_id, person_id, role_id = new_uuids()
    await _init_wm(app, session_id, person_id, role_id)

    async with await _client(app) as client:
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
    assert resp.json()["metadata"]["intent"] is None
    assert call.await_args.kwargs["system_prompt"] == STORY_PROMPT
