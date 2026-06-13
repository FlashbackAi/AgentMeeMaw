"""End-to-end test: user_id travels request → Orchestrator → Working Memory.

Entry point: POST /session/start with a concrete user_id UUID in the request
body.  The real Orchestrator (owns_working_memory=True) picks it up in
handle_session_start, passes it through to the init_working_memory step in
starter_opener.py, which calls wm.initialize(user_id=...).  We then read the
WM hash back and assert state.user_id equals the UUID we sent.

This is the one path that proves propagation: the UUID is NOT passed straight
into wm.initialize by the test — it travels through the HTTP layer, the
orchestrator's state machine, and the step, exactly as it would in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import fakeredis.aioredis
import httpx
import pytest_asyncio

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.intent_classifier.schema import IntentResult
from flashback.orchestrator import Orchestrator
from flashback.response_generator import generator as generator_module
from flashback.response_generator.generator import ResponseGenerator
from flashback.working_memory import WorkingMemory
from tests.http.conftest import auth_headers

SERVICE_TOKEN = "test-token"

# A fixed UUID that the test sends in the request body; if WM stores a
# different string (e.g. "None" or "") the assertion catches it.
USER_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class FixedClassifier:
    async def classify(self, recent_turns, signals):
        return IntentResult(
            intent="story",
            confidence="high",
            emotional_temperature="medium",
            reasoning="test-controlled",
        )


class FakeDbPool:
    """Minimal DB double: persons returns a name + phase only."""

    def __init__(self) -> None:
        self.person_name = "Maya"
        self.relationship = "mother"
        self.phase = "starter"

    def connection(self):
        return _AsyncCtx(FakeConn(self))


class FakeConn:
    def __init__(self, pool: FakeDbPool) -> None:
        self.pool = pool

    def cursor(self):
        return _AsyncCtx(FakeCursor(self.pool))


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
            return None
        # theme / archetype queries — return None (no rows)
        return None

    async def fetchall(self):
        return []


class _AsyncCtx:
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


async def test_user_id_stored_in_working_memory_after_session_start(
    fake_redis, monkeypatch
):
    """user_id UUID from request body lands in WM state.user_id (not "" or "None")."""
    monkeypatch.setattr(
        generator_module,
        "call_text",
        AsyncMock(return_value="Tell me about Maya."),
    )

    cfg = HttpConfig(
        database_url="postgresql://unused/x",
        valkey_url="redis://unused/0",
        service_token=SERVICE_TOKEN,
        http_host="127.0.0.1",
        http_port=8000,
        working_memory_ttl_seconds=100,
        working_memory_transcript_limit=30,
        db_pool_min_size=1,
        db_pool_max_size=2,
    )
    app = create_app(cfg)
    app.state.redis = fake_redis
    app.state.db_pool = FakeDbPool()
    wm = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    app.state.working_memory = wm
    app.state.orchestrator = Orchestrator(
        wm=wm,
        db_pool=app.state.db_pool,
        intent_classifier=FixedClassifier(),
        response_generator=ResponseGenerator(
            settings=type("S", (), {"openai_api_key": "k", "anthropic_api_key": "k"})(),
            provider="anthropic",
            model="claude-sonnet-4-6",
            timeout=12,
            max_tokens=400,
        ),
    )

    from uuid import uuid4

    session_id = str(uuid4())
    person_id = str(uuid4())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/session/start",
            headers=auth_headers(),
            json={
                "session_id": session_id,
                "person_id": person_id,
                "user_id": str(USER_ID),
                "session_metadata": {},
            },
        )

    assert resp.status_code == 200, resp.text

    # Read back from WM — the UUID must have flowed through:
    # request body → session.py route → Orchestrator.handle_session_start →
    # init_working_memory step → wm.initialize(user_id=_user_id_str(state.user_id))
    state = await wm.get_state(session_id)
    assert state.user_id == str(USER_ID), (
        f"Expected WM user_id={str(USER_ID)!r}, got {state.user_id!r}"
    )
