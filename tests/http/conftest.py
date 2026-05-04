"""Shared HTTP test fixtures.

The default app needs Valkey + Postgres + an Orchestrator. Tests get:

* a real :class:`WorkingMemory` backed by fakeredis (so the WM
  contract is exercised end-to-end), and
* a stub orchestrator with controllable behaviour.

Postgres is *not* booted unless a test asks for it (the
``async_db_pool`` fixture below is the opt-in path for admin tests).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.orchestrator import (
    SessionStartResult,
    SessionWrapResult,
    TurnResult,
)
from flashback.orchestrator.stub import PersonNotFoundError
from flashback.working_memory import WorkingMemory


SERVICE_TOKEN = "test-token"


def _make_test_config() -> HttpConfig:
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


# --- Stub orchestrator ------------------------------------------------------


class FakeOrchestrator:
    """Test double with knobs for behaviour.

    Each handle method has a configurable return + an optional
    side-effect (e.g., raise PersonNotFoundError to simulate 404).
    """

    def __init__(self) -> None:
        self.start_result = SessionStartResult(
            opener="Tell me about Test Subject.",
            phase="starter",
            selected_question_id=None,
        )
        self.turn_result = TurnResult(
            reply="I hear you. Tell me more.",
            intent=None,
            emotional_temperature=None,
            segment_boundary=False,
        )
        self.wrap_result = SessionWrapResult(
            session_summary="",
            segments_extracted_count=0,
        )
        self.start_raises: Exception | None = None
        self.turn_raises: Exception | None = None
        self.wrap_raises: Exception | None = None

        self.start_calls: list[dict[str, Any]] = []
        self.turn_calls: list[dict[str, Any]] = []
        self.wrap_calls: list[dict[str, Any]] = []

    async def handle_session_start(self, **kwargs):
        self.start_calls.append(kwargs)
        if self.start_raises:
            raise self.start_raises
        return self.start_result

    async def handle_turn(self, **kwargs):
        self.turn_calls.append(kwargs)
        if self.turn_raises:
            raise self.turn_raises
        return self.turn_result

    async def handle_session_wrap(self, **kwargs):
        self.wrap_calls.append(kwargs)
        if self.wrap_raises:
            raise self.wrap_raises
        return self.wrap_result


# --- Fixtures ---------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest_asyncio.fixture
async def app(fake_redis, fake_orchestrator):
    """A FastAPI app with mocked dependencies on ``app.state``.

    The lifespan is *not* run — we populate state directly so the
    handlers see a working WM/orchestrator without needing a real DB
    or Valkey. This is fine because httpx's ASGITransport doesn't
    fire lifespan events.
    """
    cfg = _make_test_config()
    application = create_app(cfg)
    application.state.redis = fake_redis
    # db_pool lives on state but is never touched in these tests; a
    # placeholder None makes accidental reads fail loudly. Tests that
    # need DB access use the ``app_with_db`` fixture instead.
    application.state.db_pool = None
    application.state.working_memory = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    application.state.orchestrator = fake_orchestrator
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac


# --- DB-dependent fixtures (admin + health happy-path) ---------------------


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def async_db_pool():
    """Async psycopg pool against TEST_DATABASE_URL.

    Skips if the env var is unset. The schema is *not* re-applied here;
    we rely on the user having pointed TEST_DATABASE_URL at a database
    that the existing ``schema_applied`` (sync) fixture has prepared,
    or equivalently a freshly-migrated test DB.
    """
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; skipping DB-touching HTTP tests."
        )

    # Apply migrations once per session via the existing sync helper.
    _ensure_schema(url)

    from flashback.db.connection import make_async_pool

    pool = make_async_pool(url, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


_SCHEMA_APPLIED = False


def _ensure_schema(url: str) -> None:
    """Drop and re-apply migrations against the test DB once per session."""
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED:
        return
    from pathlib import Path

    import psycopg

    repo_root = Path(__file__).resolve().parents[2]
    migrations_dir = repo_root / "migrations"
    up_files = sorted(migrations_dir.glob("*.up.sql"))
    if not up_files:
        pytest.fail(f"no migrations found under {migrations_dir}")

    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")

    for path in up_files:
        sql = path.read_text(encoding="utf-8")
        with psycopg.connect(url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    _SCHEMA_APPLIED = True


@pytest_asyncio.fixture
async def app_with_db(fake_redis, fake_orchestrator, async_db_pool):
    cfg = _make_test_config()
    application = create_app(cfg)
    application.state.redis = fake_redis
    application.state.db_pool = async_db_pool
    application.state.working_memory = WorkingMemory(
        redis_client=fake_redis,
        ttl_seconds=cfg.working_memory_ttl_seconds,
        transcript_limit=cfg.working_memory_transcript_limit,
    )
    application.state.orchestrator = fake_orchestrator
    return application


@pytest_asyncio.fixture
async def client_with_db(app_with_db):
    transport = httpx.ASGITransport(app=app_with_db)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac


# --- Convenience helpers ----------------------------------------------------


def auth_headers(token: str = SERVICE_TOKEN) -> dict[str, str]:
    return {"X-Service-Token": token}


def new_uuids() -> tuple[str, str, str]:
    return (str(uuid4()), str(uuid4()), str(uuid4()))


__all__ = [
    "FakeOrchestrator",
    "SERVICE_TOKEN",
    "auth_headers",
    "new_uuids",
    "PersonNotFoundError",
]
