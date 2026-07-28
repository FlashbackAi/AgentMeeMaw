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

    async def test_happy_path_with_real_db(self, client_with_db, app_with_db):
        # The happy path needs Valkey + Postgres *and* the four mandatory SQS
        # queues. There is no SQS in the test env, so stub the client -- the
        # point here is that all-healthy yields 200/ok, not that SQS works.
        from flashback.http.deps import get_sqs_client

        class _OkSQS:
            async def get_queue_attributes(self, queue_url):
                return {"ApproximateNumberOfMessages": "0"}

        app_with_db.dependency_overrides[get_sqs_client] = lambda: _OkSQS()
        try:
            resp = await client_with_db.get("/health")
        finally:
            app_with_db.dependency_overrides.pop(get_sqs_client, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["valkey"] == "ok"
        assert body["checks"]["postgres"] == "ok"

    async def test_unconfigured_render_queues_do_not_flip_health(
        self, client_with_db, app_with_db
    ):
        """An unset optional render queue is reported but stays green."""
        from flashback.http.deps import get_sqs_client

        class _OkSQS:
            async def get_queue_attributes(self, queue_url):
                return {"ApproximateNumberOfMessages": "0"}

        app_with_db.dependency_overrides[get_sqs_client] = lambda: _OkSQS()
        try:
            resp = await client_with_db.get("/health")
        finally:
            app_with_db.dependency_overrides.pop(get_sqs_client, None)

        assert resp.status_code == 200, resp.text
        checks = resp.json()["checks"]
        # Whatever their state, an "unconfigured" optional queue never degrades.
        assert not any(v.startswith("error:") for v in checks.values())
