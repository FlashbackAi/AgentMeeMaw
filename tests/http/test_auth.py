"""Auth dependency — service token enforcement on the protected routes."""

from __future__ import annotations

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
