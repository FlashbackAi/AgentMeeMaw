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
from flashback.phase_gate import PhaseGateError
from flashback.phase_gate.schema import SelectionResult
from flashback.orchestrator import Orchestrator
from flashback.response_generator import generator as generator_module
from flashback.response_generator.generator import ResponseGenerator
from flashback.working_memory import WorkingMemory
from tests.http.conftest import auth_headers, new_uuids

SERVICE_TOKEN = "test-token"
SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")
STARTER_Q = UUID("55555555-5555-5555-5555-555555555555")
STEADY_Q = UUID("66666666-6666-6666-6666-666666666666")


class FixedClassifier:
    def __init__(self, intent: str) -> None:
        self.intent = intent

    async def classify(self, recent_turns, signals):
        return IntentResult(
            intent=self.intent,
            confidence="high",
            emotional_temperature="medium",
            reasoning="test-controlled",
        )


class FakePhaseGate:
    def __init__(
        self,
        *,
        starter_result: SelectionResult | None = None,
        next_result: SelectionResult | None = None,
        starter_raises: Exception | None = None,
        next_raises: Exception | None = None,
    ) -> None:
        self.starter_result = starter_result or SelectionResult(
            phase="starter",
            question_id=STARTER_Q,
            question_text="What's a smell that brings them right back?",
            source="starter_anchor",
            dimension="sensory",
            rationale="test starter",
        )
        self.next_result = next_result or SelectionResult(
            phase="steady",
            question_id=STEADY_Q,
            question_text="What did she keep on the porch?",
            source="dropped_reference",
            rationale="test steady",
        )
        self.starter_raises = starter_raises
        self.next_raises = next_raises
        self.starter_calls = 0
        self.next_calls = 0

    async def select_starter_question(self, person_id):
        self.starter_calls += 1
        if self.starter_raises:
            raise self.starter_raises
        return self.starter_result

    async def select_next_question(self, person_id, session_id):
        self.next_calls += 1
        if self.next_raises:
            raise self.next_raises
        return self.next_result


class FakeDbPool:
    def __init__(self, *, phase: str = "starter") -> None:
        self.phase = phase

    def connection(self):
        return _AsyncContext(FakeConnection(phase=self.phase))


class FakeConnection:
    def __init__(self, *, phase: str) -> None:
        self.phase = phase

    def cursor(self):
        return _AsyncContext(FakeCursor(phase=self.phase))


class FakeCursor:
    def __init__(self, *, phase: str) -> None:
        self.phase = phase

    async def execute(self, sql, params=None):
        self.sql = sql

    async def fetchone(self):
        if "FROM persons" in self.sql:
            return ("Maya", "mother", self.phase, "she", None)
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


async def _app(fake_redis, *, classifier, phase_gate, person_phase: str = "starter"):
    cfg = _config()
    app = create_app(cfg)
    app.state.redis = fake_redis
    app.state.db_pool = FakeDbPool(phase=person_phase)
    app.state.working_memory = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    app.state.orchestrator = Orchestrator(
        wm=app.state.working_memory,
        db_pool=app.state.db_pool,
        intent_classifier=classifier,
        response_generator=_response_generator(),
        phase_gate=phase_gate,
    )
    return app


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


async def test_session_start_selects_starter_and_records_question(fake_redis, monkeypatch):
    call = AsyncMock(return_value="Flashback here. That smell question is a good place to begin.")
    monkeypatch.setattr(generator_module, "call_text", call)
    phase_gate = FakePhaseGate()
    app = await _app(fake_redis, classifier=FixedClassifier("story"), phase_gate=phase_gate)

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
    assert phase_gate.starter_calls == 1
    assert "What's a smell that brings them right back?" in call.await_args.kwargs["user_message"]
    state = await app.state.working_memory.get_state(session_id)
    assert state.last_seeded_question_id == str(STARTER_Q)
    assert await app.state.working_memory.get_recently_asked_question_ids(session_id) == [
        str(STARTER_Q)
    ]


async def test_session_start_steady_selects_next_question(fake_redis, monkeypatch):
    call = AsyncMock(return_value="Last time we talked about the porch. What did she keep on the porch?")
    monkeypatch.setattr(generator_module, "call_text", call)
    phase_gate = FakePhaseGate()
    app = await _app(
        fake_redis,
        classifier=FixedClassifier("story"),
        phase_gate=phase_gate,
        person_phase="steady",
    )

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
    assert phase_gate.starter_calls == 0
    assert phase_gate.next_calls == 1
    assert "<seeded_question>" in call.await_args.kwargs["user_message"]
    assert "What did she keep on the porch?" in call.await_args.kwargs["user_message"]
    state = await app.state.working_memory.get_state(session_id)
    assert state.last_seeded_question_id == str(STEADY_Q)
    assert await app.state.working_memory.get_recently_asked_question_ids(session_id) == [
        str(STEADY_Q)
    ]


async def test_turn_switch_fires_phase_gate_and_records_selection(fake_redis, monkeypatch):
    call = AsyncMock(return_value="What did she keep on the porch?")
    monkeypatch.setattr(generator_module, "call_text", call)
    phase_gate = FakePhaseGate()
    app = await _app(fake_redis, classifier=FixedClassifier("switch"), phase_gate=phase_gate)
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
                "message": "Let's talk about something else.",
            },
        )

    assert resp.status_code == 200
    assert phase_gate.next_calls == 1
    assert "<seeded_question>" in call.await_args.kwargs["user_message"]
    state = await app.state.working_memory.get_state(session_id)
    assert state.last_seeded_question_id == str(STEADY_Q)
    assert await app.state.working_memory.get_recently_asked_question_ids(session_id) == [
        str(STEADY_Q)
    ]


async def test_turn_deepen_does_not_fire_phase_gate(fake_redis, monkeypatch):
    monkeypatch.setattr(generator_module, "call_text", AsyncMock(return_value="I'm with you."))
    phase_gate = FakePhaseGate()
    app = await _app(fake_redis, classifier=FixedClassifier("deepen"), phase_gate=phase_gate)
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
                "message": "I miss her a lot today.",
            },
        )

    assert resp.status_code == 200
    assert phase_gate.next_calls == 0


async def test_turn_switch_empty_bank_generates_without_seed(fake_redis, monkeypatch):
    call = AsyncMock(return_value="We can take this in another direction.")
    monkeypatch.setattr(generator_module, "call_text", call)
    phase_gate = FakePhaseGate(
        next_result=SelectionResult(phase="steady", rationale="empty")
    )
    app = await _app(fake_redis, classifier=FixedClassifier("switch"), phase_gate=phase_gate)
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
                "message": "Let's switch.",
            },
        )

    assert resp.status_code == 200
    assert "<seeded_question>" not in call.await_args.kwargs["user_message"]
    state = await app.state.working_memory.get_state(session_id)
    assert state.last_seeded_question_id == ""


async def test_session_start_phase_gate_error_maps_to_503(fake_redis, monkeypatch):
    monkeypatch.setattr(generator_module, "call_text", AsyncMock(return_value="unused"))
    app = await _app(
        fake_redis,
        classifier=FixedClassifier("story"),
        phase_gate=FakePhaseGate(starter_raises=PhaseGateError("missing seed")),
    )

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

    assert resp.status_code == 503


async def test_turn_switch_phase_gate_error_degrades_gracefully(fake_redis, monkeypatch):
    call = AsyncMock(return_value="We can choose a gentler thread.")
    monkeypatch.setattr(generator_module, "call_text", call)
    app = await _app(
        fake_redis,
        classifier=FixedClassifier("switch"),
        phase_gate=FakePhaseGate(next_raises=PhaseGateError("temporary")),
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
                "message": "Let's switch.",
            },
        )

    assert resp.status_code == 200
    assert "<seeded_question>" not in call.await_args.kwargs["user_message"]
    state = await app.state.working_memory.get_state(session_id)
    assert state.last_seeded_question_id == ""
