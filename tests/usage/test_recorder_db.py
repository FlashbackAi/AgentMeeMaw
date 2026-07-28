import os

import psycopg
import pytest

from flashback.usage import recorder

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.fixture(autouse=True)
def _dsn(monkeypatch, schema_applied):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    recorder.reset_pool_for_tests()
    yield
    recorder.reset_pool_for_tests()


def _rows():
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT service, feature, provider, model, input_tokens, "
            "output_tokens, cost_usd FROM usage_events ORDER BY created_at"
        )
        return cur.fetchall()


def test_record_llm_usage_sync_inserts_agent_row():
    recorder.record_llm_usage_sync(
        feature="response_generate", provider="anthropic",
        model="claude-sonnet-4-6", input_tokens=1000, output_tokens=200,
    )
    rows = _rows()
    assert len(rows) == 1
    service, feature, provider, model, inp, out, cost = rows[0]
    assert (service, feature, provider, model) == (
        "agent", "response_generate", "anthropic", "claude-sonnet-4-6")
    assert inp == 1000 and out == 200
    assert float(cost) == pytest.approx(3.0 * 0.001 + 15.0 * 0.0002)


def test_insert_event_returns_id_and_forces_supplied_fields():
    new_id = recorder.insert_event({
        "service": "node", "feature": "artifact_image", "provider": "gemini",
        "model": "img-1", "input_tokens": None, "output_tokens": None,
        "cache_read_tokens": None, "cache_write_tokens": None,
        "units": 1, "unit_type": "images", "cost_usd": 0.04,
        "person_id": None, "session_id": None,
    })
    assert new_id is not None
    assert len(_rows()) == 1


def test_record_never_raises_when_database_url_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    recorder.reset_pool_for_tests()
    # Must be a silent no-op, not an exception.
    recorder.record_llm_usage_sync(
        feature="x", provider="anthropic", model="claude-haiku-4-5",
        input_tokens=1, output_tokens=1,
    )
