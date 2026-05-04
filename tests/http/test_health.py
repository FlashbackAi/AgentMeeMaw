"""``/health`` reachability tests."""

from __future__ import annotations

import pytest


class TestHealth:
    async def test_valkey_down_returns_503(self, client, fake_redis):
        # Close the fake redis client so subsequent calls raise.
        await fake_redis.aclose()
        resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        # Either valkey is reported down OR postgres is (db_pool is None
        # by design in the no-DB fixture).
        assert "valkey" in body["checks"] or "postgres" in body["checks"]

    async def test_no_db_pool_reports_postgres_error(self, client):
        resp = await client.get("/health")
        # The no-DB fixture leaves db_pool=None, so the postgres check
        # must fail. Valkey should still be ok.
        assert resp.status_code == 503
        body = resp.json()
        assert body["checks"]["valkey"] == "ok"
        assert body["checks"]["postgres"].startswith("error:")

    async def test_happy_path_with_real_db(self, client_with_db):
        # Full happy path requires both Valkey + Postgres.
        resp = await client_with_db.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["valkey"] == "ok"
        assert body["checks"]["postgres"] == "ok"
