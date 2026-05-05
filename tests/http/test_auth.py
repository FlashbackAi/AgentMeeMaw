"""Auth dependency — service token enforcement on the protected routes."""

from __future__ import annotations

import httpx

from flashback.config import HttpConfig
from flashback.http.app import create_app
from flashback.working_memory import WorkingMemory
from tests.http.conftest import auth_headers, new_uuids


class TestServiceToken:
    async def test_missing_header_returns_401(self, client):
        session_id, person_id, role_id = new_uuids()
        resp = await client.post(
            "/turn",
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "hello",
            },
        )
        assert resp.status_code == 401
        assert "invalid service token" in resp.json()["detail"]

    async def test_wrong_token_returns_401(self, client):
        session_id, person_id, role_id = new_uuids()
        resp = await client.post(
            "/turn",
            headers={"X-Service-Token": "WRONG"},
            json={
                "session_id": session_id,
                "person_id": person_id,
                "role_id": role_id,
                "message": "hello",
            },
        )
        assert resp.status_code == 401

    async def test_correct_token_passes_to_handler(self, client):
        # /turn against a non-started session is supposed to be 409, not 401.
        # A 409 here proves auth passed and the handler ran.
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

    async def test_health_does_not_require_token(self, client):
        # /health must be reachable without auth so k8s probes work.
        resp = await client.get("/health")
        # We don't pin the status — DB pool is None so it'll be 503 —
        # but importantly NOT 401.
        assert resp.status_code != 401

    async def test_auth_can_be_disabled_for_local_integration(
        self,
        fake_redis,
        fake_orchestrator,
    ):
        cfg = HttpConfig(
            database_url="postgresql://unused-in-no-db-tests/x",
            valkey_url="redis://unused-in-tests/0",
            service_token="test-token",
            http_host="127.0.0.1",
            http_port=8000,
            working_memory_ttl_seconds=100,
            working_memory_transcript_limit=30,
            db_pool_min_size=1,
            db_pool_max_size=2,
            service_token_auth_disabled=True,
        )
        app = create_app(cfg)
        app.state.redis = fake_redis
        app.state.db_pool = None
        app.state.working_memory = WorkingMemory(
            redis_client=fake_redis,
            ttl_seconds=cfg.working_memory_ttl_seconds,
            transcript_limit=cfg.working_memory_transcript_limit,
        )
        app.state.orchestrator = fake_orchestrator

        session_id, person_id, role_id = new_uuids()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/turn",
                json={
                    "session_id": session_id,
                    "person_id": person_id,
                    "role_id": role_id,
                    "message": "hello",
                },
            )

        assert resp.status_code == 409
