from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
import httpx
import pytest_asyncio

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.intent_classifier.schema import IntentResult
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


class SequenceDetector:
    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, **kwargs):
        self.calls += 1
        if self.calls < 3:
            return SegmentDetectionResult(
                boundary_detected=False,
                reasoning="The segment is still open.",
            )
        return SegmentDetectionResult(
            boundary_detected=True,
            rolling_summary="The contributor talked about Sunday pasta.",
            reasoning="The segment has wrapped.",
        )


class CapturingExtractionQueue:
    def __init__(self) -> None:
        self.calls = []

    async def push(self, **kwargs):
        self.calls.append(kwargs)
        return "msg-sequence"


class FakeDbPool:
    def connection(self):
        raise AssertionError("DB should not be touched in this sequence test")


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


async def test_five_turn_sequence_closes_one_segment(fake_redis):
    cfg = _config()
    app = create_app(cfg)
    wm = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    detector = SequenceDetector()
    queue = CapturingExtractionQueue()
    app.state.working_memory = wm
    app.state.orchestrator = Orchestrator(
        OrchestratorDeps(
            db_pool=FakeDbPool(),
            working_memory=wm,
            intent_classifier=FixedClassifier(),
            retrieval=None,
            phase_gate=None,
            response_generator=None,
            segment_detector=detector,
            extraction_queue=queue,
            settings=SimpleNamespace(segment_detector_min_turns=4),
        )
    )
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()
    await wm.initialize(
        str(session_id),
        str(person_id),
        str(role_id),
        datetime.now(timezone.utc),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        boundaries = []
        for i in range(5):
            resp = await client.post(
                "/turn",
                headers=auth_headers(),
                json={
                    "session_id": str(session_id),
                    "person_id": str(person_id),
                    "role_id": str(role_id),
                    "message": f"Memory detail {i}",
                },
            )
            assert resp.status_code == 200
            boundaries.append(resp.json()["metadata"]["segment_boundary"])

    assert boundaries == [False, False, False, True, False]
    assert detector.calls == 3
    assert len(queue.calls) == 1
    assert queue.calls[0]["rolling_summary"] == (
        "The contributor talked about Sunday pasta."
    )
    state = await wm.get_state(str(session_id))
    assert state.rolling_summary == "The contributor talked about Sunday pasta."
    assert len(await wm.get_segment(str(session_id))) == 2
