from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import fakeredis.aioredis
import httpx
import pytest_asyncio
from structlog.testing import capture_logs

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.intent_classifier.schema import IntentResult
from flashback.llm.errors import LLMError
from flashback.orchestrator import Orchestrator, OrchestratorDeps
from flashback.segment_detector.schema import SegmentDetectionResult
from flashback.working_memory import WorkingMemory
from tests.http.conftest import auth_headers

SERVICE_TOKEN = "test-token"


class FixedClassifier:
    async def classify(self, recent_turns, signals):
        return IntentResult(
            intent="story",
            confidence="high",
            emotional_temperature="medium",
            reasoning="test-controlled",
        )


class FailingClassifier:
    async def classify(self, recent_turns, signals):
        raise LLMError("classifier unavailable")


class BoundaryDetector:
    async def detect(self, **kwargs):
        return SegmentDetectionResult(
            boundary_detected=True,
            rolling_summary="The contributor talked about Sunday pasta.",
            reasoning="The segment reached a natural close.",
        )


class CapturingExtractionQueue:
    def __init__(self) -> None:
        self.calls = []

    async def push(self, **kwargs):
        self.calls.append(kwargs)
        return "msg-boundary"


class FakeDbPool:
    def connection(self):
        return _AsyncContext(FakeConnection())


class FakeConnection:
    def cursor(self):
        return _AsyncContext(FakeCursor())


class FakeCursor:
    async def execute(self, sql, params=None):
        self.sql = sql

    async def fetchone(self):
        if "FROM persons" in self.sql:
            return ("Maya", "mother", "starter")
        raise AssertionError(f"unexpected SQL: {self.sql}")


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


def _orchestrator(wm: WorkingMemory, classifier) -> Orchestrator:
    return Orchestrator(
        OrchestratorDeps(
            db_pool=FakeDbPool(),
            working_memory=wm,
            intent_classifier=classifier,
            retrieval=None,
            phase_gate=None,
            response_generator=None,
            settings=None,
        )
    )


def _orchestrator_with_boundary_detector(wm: WorkingMemory) -> Orchestrator:
    return Orchestrator(
        OrchestratorDeps(
            db_pool=FakeDbPool(),
            working_memory=wm,
            intent_classifier=FixedClassifier(),
            retrieval=None,
            phase_gate=None,
            response_generator=None,
            segment_detector=BoundaryDetector(),
            extraction_queue=CapturingExtractionQueue(),
            settings=SimpleNamespace(segment_detector_min_turns=2),
        )
    )


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _app(fake_redis, classifier):
    cfg = _config()
    app = create_app(cfg)
    wm = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    app.state.redis = fake_redis
    app.state.db_pool = FakeDbPool()
    app.state.working_memory = wm
    app.state.orchestrator = _orchestrator(wm, classifier)
    return app


async def test_turn_logs_correlation_ids_and_steps(fake_redis):
    app = await _app(fake_redis, FixedClassifier())
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()
    await app.state.working_memory.initialize(
        str(session_id),
        str(person_id),
        str(role_id),
        datetime.now(timezone.utc),
    )

    async with await _client(app) as client:
        with capture_logs() as logs:
            resp = await client.post(
                "/turn",
                headers=auth_headers(),
                json={
                    "session_id": str(session_id),
                    "person_id": str(person_id),
                    "role_id": str(role_id),
                    "message": "She loved making pasta from scratch.",
                },
            )

    assert resp.status_code == 200
    turn_logs = [record for record in logs if record["event"] == "turn_complete"]
    assert len(turn_logs) == 1
    assert UUID(turn_logs[0]["turn_id"])
    assert turn_logs[0]["session_id"] == str(session_id)
    assert turn_logs[0]["person_id"] == str(person_id)

    step_names = {
        record["step"]
        for record in logs
        if record["event"] == "step_complete"
    }
    assert {
        "append_user_turn",
        "intent_classify",
        "generate_response",
        "append_assistant",
    } <= step_names
    assert all(
        "duration_ms" in record
        for record in logs
        if record["event"] == "step_complete"
    )


async def test_degraded_step_logs_error_type(fake_redis):
    app = await _app(fake_redis, FailingClassifier())
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()
    await app.state.working_memory.initialize(
        str(session_id),
        str(person_id),
        str(role_id),
        datetime.now(timezone.utc),
    )

    async with await _client(app) as client:
        with capture_logs() as logs:
            resp = await client.post(
                "/turn",
                headers=auth_headers(),
                json={
                    "session_id": str(session_id),
                    "person_id": str(person_id),
                    "role_id": str(role_id),
                    "message": "She loved making pasta from scratch.",
                },
            )

    assert resp.status_code == 200
    degraded = [record for record in logs if record["event"] == "step_degraded"]
    assert len(degraded) == 1
    assert degraded[0]["step"] == "intent_classify"
    assert degraded[0]["error"] == "LLMError"


async def test_boundary_turn_logs_detect_segment_message_id(fake_redis):
    cfg = _config()
    app = create_app(cfg)
    wm = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    app.state.redis = fake_redis
    app.state.db_pool = FakeDbPool()
    app.state.working_memory = wm
    app.state.orchestrator = _orchestrator_with_boundary_detector(wm)
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()
    await app.state.working_memory.initialize(
        str(session_id),
        str(person_id),
        str(role_id),
        datetime.now(timezone.utc),
    )

    async with await _client(app) as client:
        with capture_logs() as logs:
            resp = await client.post(
                "/turn",
                headers=auth_headers(),
                json={
                    "session_id": str(session_id),
                    "person_id": str(person_id),
                    "role_id": str(role_id),
                    "message": "She loved making pasta from scratch.",
                },
            )

    assert resp.status_code == 200
    assert resp.json()["metadata"]["segment_boundary"] is True
    boundary_logs = [
        record
        for record in logs
        if record["event"] == "step_complete"
        and record.get("step") == "detect_segment"
        and record.get("boundary") is True
    ]
    assert len(boundary_logs) == 1
    assert boundary_logs[0]["sqs_message_id"] == "msg-boundary"


async def test_session_start_logs_bound_correlation_ids(fake_redis):
    app = await _app(fake_redis, FixedClassifier())
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()

    async with await _client(app) as client:
        with capture_logs() as logs:
            resp = await client.post(
                "/session/start",
                headers=auth_headers(),
                json={
                    "session_id": str(session_id),
                    "person_id": str(person_id),
                    "role_id": str(role_id),
                    "session_metadata": {},
                },
            )

    assert resp.status_code == 200
    start_logs = [
        record for record in logs if record["event"] == "session_start_complete"
    ]
    assert len(start_logs) == 1
    assert start_logs[0]["session_id"] == str(session_id)
    assert start_logs[0]["person_id"] == str(person_id)
