# Observability & Cost Dashboard — Implementation Plan (Python agent side)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Meter every Python-side LLM + embedding call into a Postgres `usage_events` ledger, expose read views for a dashboard, and provide a `POST /usage/events` endpoint the Node backend calls to record its own artifact-generation cost — the agent is the sole writer.

**Architecture:** A single append-only `usage_events` table. A `flashback.usage` module computes cost from a code-side pricing map and inserts rows. All text-LLM calls funnel through `src/flashback/llm/interface.py`, where usage is captured; Voyage embeddings are captured at their three call sites. Node's artifact cost arrives via `POST /usage/events` (agent performs the insert). `dashboard_*` views aggregate cost + operational counts for Node to read and serve to the separate dashboard UI repo.

**Tech Stack:** Python, FastAPI, psycopg v3 (async pool for HTTP, sync pool for workers), structlog, pytest + pytest-asyncio, Postgres. Migrations run via `python scripts/migrate.py`.

**Spec:** `docs/superpowers/specs/2026-07-07-observability-dashboard-design.md`

## Global Constraints

- **The agent is the sole writer to `usage_events`.** Python inserts its own LLM/embedding rows; Node's rows arrive only through `POST /usage/events`, whose handler forces `service='node'`. Node never inserts directly. (CLAUDE.md §3.)
- **Metering must never break the caller.** Every insert path is wrapped in `try/except Exception` that logs `usage.record_failed` and returns — a failed ledger write never propagates into a turn, a worker, or the endpoint's own work.
- **`cost_usd` is stored, not re-derived.** Each service computes its own dollar figure; the ledger sums a number.
- **No new embedding work.** `usage_events` has no vector column and never touches the embedding queue.
- **Pricing lives in code** (`flashback/usage/pricing.py`), keyed by `(provider, model)`. Anthropic rates are known (below); OpenAI `gpt-5.1` and Voyage `voyage-3-large` rates are marked `# VERIFY` — confirm against the provider pricing pages before production. Tests use explicit test rates, never the production map, so test correctness does not depend on the VERIFY values.
- **Feature taxonomy** (the `feature` column value per call site) is fixed in this plan's Task 6 table. An unknown `feature` is stored as-is; the ledger never rejects on taxonomy.
- **DB access:** async paths use `asyncio.to_thread(_insert_sync, …)` against a lazily-created **sync** pool (`make_pool`), so the recorder is not bound to any event loop and works identically in the long-lived HTTP loop and in workers. `DATABASE_URL` must be set (already required service-wide); if unset, inserts are silent no-ops.
- **Tests that touch Postgres** require `TEST_DATABASE_URL` (Postgres + pgvector on `:15432`); they skip when it is unset. Run the DB containers with `docker start` first.

Anthropic pricing (per 1M tokens), 5-minute ephemeral cache (the TTL this repo uses at `interface.py`):

| provider | model | input | output | cache_read (0.1×) | cache_write (1.25×) |
|---|---|---|---|---|---|
| anthropic | claude-sonnet-4-6 | 3.00 | 15.00 | 0.30 | 3.75 |
| anthropic | claude-haiku-4-5 | 1.00 | 5.00 | 0.10 | 1.25 |

---

### Task 1: Migration — `usage_events` table + `dashboard_*` views

**Files:**
- Create: `migrations/0037_usage_events.up.sql`
- Create: `migrations/0037_usage_events.down.sql`
- Test: `tests/db/test_usage_events_schema.py`

**Interfaces:**
- Produces: table `usage_events` with columns `id, service, feature, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, units, unit_type, cost_usd, person_id, session_id, created_at`; views `dashboard_cost_by_feature`, `dashboard_cost_by_model`, `dashboard_storybooks`, `dashboard_tributes`, `dashboard_content_counts`, `dashboard_worker_health`.

- [ ] **Step 1: Write the failing test**

`tests/db/test_usage_events_schema.py`:

```python
import os
import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def test_usage_events_table_and_views_exist(schema_applied):
    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'usage_events' ORDER BY column_name"
        )
        cols = {r[0] for r in cur.fetchall()}
        assert {
            "id", "service", "feature", "provider", "model",
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "units", "unit_type", "cost_usd",
            "person_id", "session_id", "created_at",
        } <= cols

        for view in (
            "dashboard_cost_by_feature",
            "dashboard_cost_by_model",
            "dashboard_storybooks",
            "dashboard_tributes",
            "dashboard_content_counts",
            "dashboard_worker_health",
        ):
            cur.execute("SELECT to_regclass(%s)", (view,))
            assert cur.fetchone()[0] is not None, f"missing view {view}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_usage_events_schema.py -v`
Expected: FAIL — `usage_events` columns not found (table doesn't exist yet).

- [ ] **Step 3: Write the up migration**

`migrations/0037_usage_events.up.sql`:

```sql
-- ============================================================================
-- 0037_usage_events.up.sql
-- Cost/usage telemetry ledger + dashboard read views (observability dashboard).
-- The agent is the sole writer. Node's artifact-generation rows arrive via
-- POST /usage/events; Node never inserts directly. Append-only; not canonical
-- graph (no status/supersession, no embedding).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS usage_events (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service            text          NOT NULL,            -- 'agent' | 'node'
    feature            text          NOT NULL,
    provider           text          NOT NULL,
    model              text          NOT NULL,
    input_tokens       int,
    output_tokens      int,
    cache_read_tokens  int,
    cache_write_tokens int,
    units              numeric,
    unit_type          text          NOT NULL DEFAULT 'tokens',
    cost_usd           numeric(12,6) NOT NULL,
    person_id          uuid,
    session_id         uuid,
    created_at         timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_events_created_at_idx ON usage_events (created_at DESC);
CREATE INDEX IF NOT EXISTS usage_events_feature_idx    ON usage_events (feature);
CREATE INDEX IF NOT EXISTS usage_events_model_idx      ON usage_events (provider, model);

-- Cost aggregates. Raw (not windowed): rows carry created_at, so the serving
-- layer windows in its own query without a view change.
CREATE OR REPLACE VIEW dashboard_cost_by_feature AS
SELECT feature,
       count(*)                       AS call_count,
       coalesce(sum(cost_usd), 0)     AS cost_usd,
       coalesce(sum(input_tokens), 0) AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens
FROM usage_events
GROUP BY feature;

CREATE OR REPLACE VIEW dashboard_cost_by_model AS
SELECT provider, model,
       count(*)                       AS call_count,
       coalesce(sum(cost_usd), 0)     AS cost_usd,
       coalesce(sum(input_tokens), 0) AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens
FROM usage_events
GROUP BY provider, model;

-- Operational counts (read from base tables to keep the down-migration trivial).
CREATE OR REPLACE VIEW dashboard_storybooks AS
SELECT status, collection, count(*) AS n
FROM storybooks
GROUP BY status, collection;

CREATE OR REPLACE VIEW dashboard_tributes AS
SELECT status, count(*) AS n
FROM tributes
GROUP BY status;

CREATE OR REPLACE VIEW dashboard_content_counts AS
SELECT
    (SELECT count(*) FROM moments  WHERE status = 'active') AS active_moments,
    (SELECT count(*) FROM entities WHERE status = 'active') AS active_entities,
    (SELECT count(*) FROM threads  WHERE status = 'active') AS active_threads,
    (SELECT count(*) FROM traits   WHERE status = 'active') AS active_traits,
    (SELECT count(*) FROM questions WHERE status = 'active') AS active_questions,
    (SELECT count(*) FROM persons) AS persons,
    (SELECT count(*) FROM persons WHERE phase = 'starter') AS persons_starter,
    (SELECT count(*) FROM persons WHERE phase = 'steady')  AS persons_steady;

CREATE OR REPLACE VIEW dashboard_worker_health AS
SELECT status, count(*) AS n, coalesce(max(attempts), 0) AS max_attempts
FROM extraction_outbox
GROUP BY status;

COMMIT;
```

- [ ] **Step 4: Write the down migration**

`migrations/0037_usage_events.down.sql`:

```sql
-- ============================================================================
-- 0037_usage_events.down.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS dashboard_worker_health;
DROP VIEW IF EXISTS dashboard_content_counts;
DROP VIEW IF EXISTS dashboard_tributes;
DROP VIEW IF EXISTS dashboard_storybooks;
DROP VIEW IF EXISTS dashboard_cost_by_model;
DROP VIEW IF EXISTS dashboard_cost_by_feature;

DROP TABLE IF EXISTS usage_events;

COMMIT;
```

- [ ] **Step 5: Apply migrations and run the test**

Run: `python scripts/migrate.py` then `pytest tests/db/test_usage_events_schema.py -v`
(The `schema_applied` fixture in `tests/conftest.py` applies every `*.up.sql` fresh, so the test does not depend on the dev DB. Running `migrate.py` verifies the migration applies cleanly against a real DB.)
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add migrations/0037_usage_events.up.sql migrations/0037_usage_events.down.sql tests/db/test_usage_events_schema.py
git commit -m "feat(usage): usage_events ledger + dashboard_* views (migration 0037)"
```

---

### Task 2: Pricing map + `compute_cost`

**Files:**
- Create: `src/flashback/usage/__init__.py`
- Create: `src/flashback/usage/pricing.py`
- Test: `tests/usage/test_pricing.py`

**Interfaces:**
- Produces: `flashback.usage.pricing.compute_cost(provider: str, model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float`. Returns USD. Unknown `(provider, model)` logs `usage.unknown_model` and returns `0.0` (never raises). Also exports `PRICING` (dict) and `ModelRate`.

- [ ] **Step 1: Write the failing test**

`tests/usage/test_pricing.py`:

```python
from flashback.usage import pricing
from flashback.usage.pricing import ModelRate, compute_cost


def test_compute_cost_sums_each_bucket_at_its_rate(monkeypatch):
    monkeypatch.setitem(
        pricing.PRICING, ("test", "m1"),
        ModelRate(input_per_mtok=10.0, output_per_mtok=20.0,
                  cache_read_per_mtok=1.0, cache_write_per_mtok=12.5),
    )
    # 1M input @10 + 1M output @20 + 1M cache_read @1 + 1M cache_write @12.5
    cost = compute_cost(
        "test", "m1",
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_read_tokens=1_000_000, cache_write_tokens=1_000_000,
    )
    assert cost == 10.0 + 20.0 + 1.0 + 12.5


def test_compute_cost_scales_with_token_count(monkeypatch):
    monkeypatch.setitem(
        pricing.PRICING, ("test", "m2"),
        ModelRate(input_per_mtok=3.0, output_per_mtok=15.0,
                  cache_read_per_mtok=0.3, cache_write_per_mtok=3.75),
    )
    cost = compute_cost("test", "m2", input_tokens=500_000, output_tokens=100_000)
    assert cost == 3.0 * 0.5 + 15.0 * 0.1


def test_unknown_model_returns_zero_and_does_not_raise():
    assert compute_cost("nope", "nope", input_tokens=1000, output_tokens=1000) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/usage/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: flashback.usage`.

- [ ] **Step 3: Write the implementation**

`src/flashback/usage/__init__.py`:

```python
"""Cost/usage metering for the agent service (observability dashboard)."""
```

`src/flashback/usage/pricing.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger("flashback.usage")


@dataclass(frozen=True)
class ModelRate:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0


# Keyed by (provider, model). Rates are USD per 1,000,000 tokens.
# Anthropic rates are current (5-minute ephemeral cache: read 0.1x, write 1.25x).
# VERIFY the OpenAI and Voyage rates against the provider pricing pages before
# relying on the dashboard's dollar totals; they do not affect test correctness.
PRICING: dict[tuple[str, str], ModelRate] = {
    ("anthropic", "claude-sonnet-4-6"): ModelRate(3.0, 15.0, 0.30, 3.75),
    ("anthropic", "claude-haiku-4-5"): ModelRate(1.0, 5.0, 0.10, 1.25),
    ("openai", "gpt-5.1"): ModelRate(1.25, 10.0, 0.125, 0.0),  # VERIFY
    ("voyage", "voyage-3-large"): ModelRate(0.18, 0.0, 0.0, 0.0),  # VERIFY (input-only)
    ("voyage", "voyage-3"): ModelRate(0.06, 0.0, 0.0, 0.0),  # VERIFY (input-only)
}


def compute_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    rate = PRICING.get((provider, model))
    if rate is None:
        log.warning("usage.unknown_model", provider=provider, model=model)
        return 0.0
    return (
        (input_tokens or 0) * rate.input_per_mtok
        + (output_tokens or 0) * rate.output_per_mtok
        + (cache_read_tokens or 0) * rate.cache_read_per_mtok
        + (cache_write_tokens or 0) * rate.cache_write_per_mtok
    ) / 1_000_000.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/usage/test_pricing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/usage/__init__.py src/flashback/usage/pricing.py tests/usage/test_pricing.py
git commit -m "feat(usage): pricing map + compute_cost"
```

---

### Task 3: Provider usage extractors

**Files:**
- Create: `src/flashback/usage/extract.py`
- Test: `tests/usage/test_extract.py`

**Interfaces:**
- Produces: `flashback.usage.extract.usage_from_anthropic(response) -> dict` and `usage_from_openai(response) -> dict`, each returning `{"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}` with int values (0 when absent). Both are defensive — they read attributes via `getattr` and never raise.

- [ ] **Step 1: Write the failing test**

`tests/usage/test_extract.py`:

```python
from types import SimpleNamespace

from flashback.usage.extract import usage_from_anthropic, usage_from_openai


def test_usage_from_anthropic_reads_all_buckets():
    resp = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=100, output_tokens=50,
        cache_read_input_tokens=200, cache_creation_input_tokens=30,
    ))
    assert usage_from_anthropic(resp) == {
        "input_tokens": 100, "output_tokens": 50,
        "cache_read_tokens": 200, "cache_write_tokens": 30,
    }


def test_usage_from_openai_reads_prompt_and_cached():
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=100, completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40),
    ))
    out = usage_from_openai(resp)
    # OpenAI prompt_tokens INCLUDES cached; split so cache_read is priced separately.
    assert out == {
        "input_tokens": 60, "output_tokens": 50,
        "cache_read_tokens": 40, "cache_write_tokens": 0,
    }


def test_extractors_are_defensive_on_missing_usage():
    empty = SimpleNamespace(usage=None)
    zero = {"input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert usage_from_anthropic(empty) == zero
    assert usage_from_openai(empty) == zero
    assert usage_from_anthropic(SimpleNamespace()) == zero
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/usage/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: flashback.usage.extract`.

- [ ] **Step 3: Write the implementation**

`src/flashback/usage/extract.py`:

```python
from __future__ import annotations

from typing import Any

_ZERO = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def usage_from_anthropic(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return dict(_ZERO)
    return {
        "input_tokens": _int(getattr(usage, "input_tokens", 0)),
        "output_tokens": _int(getattr(usage, "output_tokens", 0)),
        "cache_read_tokens": _int(getattr(usage, "cache_read_input_tokens", 0)),
        "cache_write_tokens": _int(getattr(usage, "cache_creation_input_tokens", 0)),
    }


def usage_from_openai(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return dict(_ZERO)
    prompt = _int(getattr(usage, "prompt_tokens", 0))
    details = getattr(usage, "prompt_tokens_details", None)
    cached = _int(getattr(details, "cached_tokens", 0)) if details is not None else 0
    # OpenAI's prompt_tokens is inclusive of cached tokens; split so the cached
    # portion is priced at the cache-read rate and the rest at full input rate.
    return {
        "input_tokens": max(prompt - cached, 0),
        "output_tokens": _int(getattr(usage, "completion_tokens", 0)),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/usage/test_extract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/usage/extract.py tests/usage/test_extract.py
git commit -m "feat(usage): provider usage extractors (anthropic/openai)"
```

---

### Task 4: Recorder — SQL + insert + record helpers

**Files:**
- Create: `src/flashback/usage/queries.py`
- Create: `src/flashback/usage/recorder.py`
- Test: `tests/usage/test_recorder_db.py`

**Interfaces:**
- Consumes: `flashback.usage.pricing.compute_cost`; `flashback.db.connection.make_pool`.
- Produces:
  - `flashback.usage.queries.INSERT_USAGE_EVENT` (SQL string, named `%(...)s` params, `RETURNING id::text`).
  - `flashback.usage.recorder.record_llm_usage_sync(*, feature, provider, model, input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0, person_id=None, session_id=None) -> None` — computes cost, inserts one `service='agent'`, `unit_type='tokens'` row; swallows+logs on failure.
  - `async flashback.usage.recorder.record_llm_usage(*, …same kwargs…) -> None` — `await asyncio.to_thread(record_llm_usage_sync, **kwargs)`.
  - `flashback.usage.recorder.insert_event(row: dict) -> str | None` — low-level insert used by the endpoint (Task 8); returns new id or `None` on failure. Wraps `INSERT_USAGE_EVENT`.
  - `flashback.usage.recorder.reset_pool_for_tests()` — clears the cached pool (test hook).

- [ ] **Step 1: Write the failing test**

`tests/usage/test_recorder_db.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/usage/test_recorder_db.py -v`
Expected: FAIL — `AttributeError: module 'flashback.usage.recorder' has no attribute 'record_llm_usage_sync'` (module missing).

- [ ] **Step 3: Write the SQL**

`src/flashback/usage/queries.py`:

```python
INSERT_USAGE_EVENT = """
INSERT INTO usage_events (
    service, feature, provider, model,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    units, unit_type, cost_usd, person_id, session_id
) VALUES (
    %(service)s, %(feature)s, %(provider)s, %(model)s,
    %(input_tokens)s, %(output_tokens)s, %(cache_read_tokens)s, %(cache_write_tokens)s,
    %(units)s, %(unit_type)s, %(cost_usd)s, %(person_id)s, %(session_id)s
)
RETURNING id::text
"""
```

- [ ] **Step 4: Write the recorder**

`src/flashback/usage/recorder.py`:

```python
from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from flashback.db.connection import make_pool
from flashback.usage.pricing import compute_cost
from flashback.usage.queries import INSERT_USAGE_EVENT

log = structlog.get_logger("flashback.usage")

_pool = None  # lazily created sync pool; not bound to any event loop


def reset_pool_for_tests() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001
            pass
    _pool = None


def _get_pool():
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            return None
        _pool = make_pool(dsn, max_size=2)
    return _pool


def insert_event(row: dict[str, Any]) -> str | None:
    """Insert one usage_events row. Never raises — logs and returns None on failure."""
    pool = _get_pool()
    if pool is None:
        return None
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_USAGE_EVENT, row)
                return cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("usage.record_failed", feature=row.get("feature"), error=str(exc))
        return None


def record_llm_usage_sync(
    *,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    person_id: str | None = None,
    session_id: str | None = None,
) -> None:
    cost = compute_cost(
        provider, model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
    )
    insert_event({
        "service": "agent",
        "feature": feature,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "units": None,
        "unit_type": "tokens",
        "cost_usd": cost,
        "person_id": person_id,
        "session_id": session_id,
    })


async def record_llm_usage(
    *,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    person_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Async entry: runs the sync insert in a thread so it never blocks the loop
    and never binds a pool to a per-message worker loop."""
    await asyncio.to_thread(
        record_llm_usage_sync,
        feature=feature, provider=provider, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
        person_id=person_id, session_id=session_id,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/usage/test_recorder_db.py -v`
Expected: PASS (all three; the last runs even without `TEST_DATABASE_URL`? No — the module-level skip gates them all. Confirm the DB is up so they run).

- [ ] **Step 6: Commit**

```bash
git add src/flashback/usage/queries.py src/flashback/usage/recorder.py tests/usage/test_recorder_db.py
git commit -m "feat(usage): recorder (sync insert + async wrapper + endpoint insert)"
```

---

### Task 5: Capture usage in `llm/interface.py`

**Files:**
- Modify: `src/flashback/llm/interface.py`
- Test: `tests/llm/test_interface_usage.py`

**Interfaces:**
- Consumes: `flashback.usage.recorder.record_llm_usage`, `flashback.usage.extract.usage_from_anthropic`, `usage_from_openai`.
- Produces: `call_with_tool`, `call_text`, `call_text_stream` each accept a new keyword-only param `feature: str = "unknown"` and thread it to the provider helpers, which record usage after the response. Streaming captures usage from the final message.

Threading map (add `feature: str = "unknown"` to the signature and pass it down):
- `call_with_tool` → `_call_anthropic(feature=…)` / `_call_openai(feature=…)`
- `call_text` → `_call_anthropic_text(feature=…)` / `_call_openai_text(feature=…)`
- `call_text_stream` → `_call_anthropic_text_stream(feature=…)` / `_call_openai_text_stream(feature=…)`

- [ ] **Step 1: Write the failing test**

`tests/llm/test_interface_usage.py`:

```python
from types import SimpleNamespace

import pytest

import flashback.llm.interface as interface


class _FakeAnthropicMessage:
    def __init__(self):
        self.stop_reason = "tool_use"
        self.usage = SimpleNamespace(
            input_tokens=120, output_tokens=40,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        self.content = [SimpleNamespace(type="tool_use", input={"ok": True})]


@pytest.mark.asyncio
async def test_call_with_tool_records_usage(monkeypatch):
    captured = {}

    async def _fake_record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(interface.usage_recorder, "record_llm_usage", _fake_record)

    async def _fake_retries(factory):
        return _FakeAnthropicMessage()

    monkeypatch.setattr(interface, "_with_provider_retries", _fake_retries)

    # Minimal settings stub — only the attributes the anthropic path reads.
    settings = SimpleNamespace()
    monkeypatch.setattr(interface, "_anthropic_client", lambda s: SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: None)))

    result = await interface.call_with_tool(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="s", user_message="u",
        tool={"name": "t", "description": "d", "input_schema": {"type": "object"}},
        max_tokens=100, timeout=5.0, settings=settings, feature="response_generate",
    )
    assert result == {"ok": True}
    assert captured["feature"] == "response_generate"
    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["input_tokens"] == 120
    assert captured["output_tokens"] == 40
```

> Note: the exact `monkeypatch` targets (`_with_provider_retries`, `_anthropic_client`) must match the real symbol names in `interface.py`. Read the file first and adjust the patch targets to the actual client accessor; the assertion on `captured` is the invariant that matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/llm/test_interface_usage.py -v`
Expected: FAIL — `call_with_tool` has no `feature` kwarg / `interface.usage_recorder` undefined.

- [ ] **Step 3: Add imports and thread `feature` through the non-streaming paths**

At the top of `src/flashback/llm/interface.py`, add:

```python
from flashback.usage import extract as usage_extract
from flashback.usage import recorder as usage_recorder
```

Add `feature: str = "unknown"` to the keyword-only signatures of `call_with_tool` (line ~40), `call_text` (~75), `call_text_stream` (~107), and pass `feature=feature` into each `_call_*` dispatch. Add `feature: str` to the six `_call_*` helper signatures.

In `_call_anthropic` (after `response = await _with_provider_retries(...)`, ~line 161) and in `_call_anthropic_text` (~223), before returning, add:

```python
        await usage_recorder.record_llm_usage(
            feature=feature, provider="anthropic", model=model,
            **usage_extract.usage_from_anthropic(response),
        )
```

In `_call_openai` (~263) and `_call_openai_text` (~351), before returning, add:

```python
        await usage_recorder.record_llm_usage(
            feature=feature, provider="openai", model=model,
            **usage_extract.usage_from_openai(response),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/llm/test_interface_usage.py -v`
Expected: PASS.

- [ ] **Step 5: Capture streaming usage**

In `_call_anthropic_text_stream` (~379): after the `async for chunk in stream.text_stream:` loop completes (still inside `async with client.messages.stream(...) as stream:`), add:

```python
        final = await stream.get_final_message()
        await usage_recorder.record_llm_usage(
            feature=feature, provider="anthropic", model=model,
            **usage_extract.usage_from_anthropic(final),
        )
```

In `_call_openai_text_stream` (~420): add `stream_options={"include_usage": True}` to the `client.chat.completions.create(...)` call, capture the final usage-bearing chunk during iteration, and record after the loop:

```python
        final_usage = None
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                final_usage = chunk           # terminal chunk: empty choices + usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        if final_usage is not None:
            await usage_recorder.record_llm_usage(
                feature=feature, provider="openai", model=model,
                **usage_extract.usage_from_openai(final_usage),
            )
```

Add a streaming test to `tests/llm/test_interface_usage.py`:

```python
@pytest.mark.asyncio
async def test_call_text_stream_records_usage_from_final_message(monkeypatch):
    captured = {}

    async def _fake_record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(interface.usage_recorder, "record_llm_usage", _fake_record)

    class _FakeStream:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        @property
        def text_stream(self):
            async def _gen():
                yield "hello"
            return _gen()
        async def get_final_message(self):
            return SimpleNamespace(usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0))

    monkeypatch.setattr(interface, "_anthropic_client", lambda s: SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStream())))

    settings = SimpleNamespace()
    chunks = []
    async for c in interface.call_text_stream(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="s", user_message="u",
        max_tokens=100, timeout=5.0, settings=settings, feature="response_generate",
    ):
        chunks.append(c)
    assert chunks == ["hello"]
    assert captured["input_tokens"] == 10 and captured["output_tokens"] == 5
```

> Adjust the `_anthropic_client` patch target and the stream accessor to the real names in `interface.py`.

- [ ] **Step 6: Run all interface + usage tests**

Run: `pytest tests/llm/test_interface_usage.py tests/usage -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/flashback/llm/interface.py tests/llm/test_interface_usage.py
git commit -m "feat(usage): capture LLM token usage in interface (incl. streaming)"
```

---

### Task 6: Label all interface call sites with `feature`

**Files:**
- Modify (one-line each — add `feature="<value>"` to the `call_with_tool` / `call_text` / `call_text_stream` invocation):

| feature value | file:line |
|---|---|
| `intent_classify` | `src/flashback/intent_classifier/classifier.py:42` |
| `segment_detect` | `src/flashback/segment_detector/detector.py:52` |
| `tap_options` | `src/flashback/orchestrator/tap_options.py:143` |
| `ground_truth_tap` | `src/flashback/ground_truth/selection_llm.py:136` |
| `onboarding_parse` | `src/flashback/onboarding/free_text_parser.py:121` |
| `identity_verify` | `src/flashback/identity_merges/verifier.py:84` |
| `profile_facts` | `src/flashback/profile_facts/extraction.py:67` |
| `node_edit` | `src/flashback/node_edits/llm.py:44` |
| `theme_archetype` | `src/flashback/themes/archetype_llm.py:224` |
| `extraction` | `src/flashback/workers/extraction/extraction_llm.py:119` |
| `extraction_compat` | `src/flashback/workers/extraction/compatibility_llm.py:61` |
| `entity_merge` | `src/flashback/workers/extraction/entity_merge_llm.py:93` |
| `trait_merge` | `src/flashback/workers/extraction/trait_merge_llm.py:62` |
| `thread_naming` | `src/flashback/workers/thread_detector/naming_llm.py:54` |
| `p4_questions` | `src/flashback/workers/thread_detector/p4_llm.py:54` |
| `trait_synth` | `src/flashback/workers/trait_synthesizer/synth_llm.py:49` |
| `producer` | `src/flashback/workers/producers/life_period.py:88` |
| `producer` | `src/flashback/workers/producers/universal.py:142` |
| `producer` | `src/flashback/workers/producers/underdeveloped.py:197` |
| `tribute_assembly` | `src/flashback/tribute/assembly.py:504` |
| `tribute_message` | `src/flashback/tribute/message_llm.py:73` |
| `tribute_video` | `src/flashback/tribute_video/edit_suggestions.py:144` |
| `tribute_video` | `src/flashback/tribute_video/assembler.py:216` |
| `storybook_tagging` | `src/flashback/storybook/tagging.py:116` |
| `storybook_script` | `src/flashback/storybook/script.py:394` |
| `response_generate` | `src/flashback/response_generator/generator.py:71` |
| `response_generate` | `src/flashback/response_generator/generator.py:84` |
| `response_generate` | `src/flashback/response_generator/generator.py:99` |
| `session_summary` | `src/flashback/session_summary/generator.py:25` |
| `profile_summary` | `src/flashback/workers/profile_summary/summary_llm.py:55` |
| `response_generate` | `src/flashback/response_generator/generator.py:115` |
| `response_generate` | `src/flashback/response_generator/generator.py:130` |
| `response_generate` | `src/flashback/response_generator/generator.py:145` |

**Interfaces:**
- Consumes: `feature` kwarg added in Task 5.
- Note on `segment_detect` vs `rolling_summary`: the Segment Detector's single `call_with_tool` (`detector.py:52`) does both boundary detection and rolling-summary regeneration in one LLM call, so their cost cannot be attributed separately. Label it `segment_detect` and do **not** add a `rolling_summary` feature.

- [ ] **Step 1: Apply the edits**

For each row above, open the file at the given line and add `feature="<value>"` as a keyword argument to the `call_with_tool` / `call_text` / `call_text_stream` call. Example at `response_generator/generator.py:71`:

```python
        text = await call_text(
            provider=self._provider, model=self._model,
            system_prompt=system, user_message=user,
            max_tokens=self._max_tokens, timeout=self._timeout,
            settings=self._settings,
            feature="response_generate",   # <-- add this line
        )
```

Line numbers drift as you edit; re-grep before each edit: `Grep` for `call_with_tool(`, `call_text(`, `call_text_stream(` in the target file and add the kwarg to the call that lacks a `feature=`.

- [ ] **Step 2: Verify every call site is labeled**

Run:
```bash
grep -rnE "call_(with_tool|text|text_stream)\(" src/flashback \
  | grep -v "def call_" > /tmp/callsites.txt
grep -rLZ "feature=" /dev/null  # placeholder — instead eyeball:
```
Then confirm each call in `/tmp/callsites.txt` has a matching `feature=` within its argument list (open each and check). Expected: 33 call sites, each with a `feature=` kwarg.

- [ ] **Step 3: Run the full suite to confirm nothing broke**

Run: `pytest tests/llm tests/usage -v` and the orchestrator/worker test dirs touched (`pytest tests/orchestrator tests/workers -q`).
Expected: PASS / no new failures vs the known pre-existing list.

- [ ] **Step 4: Commit**

```bash
git add -A src/flashback
git commit -m "feat(usage): label all LLM call sites with feature"
```

---

### Task 7: Capture Voyage embedding usage (3 sites)

**Files:**
- Modify: `src/flashback/retrieval/voyage.py` (async query embedder)
- Modify: `src/flashback/workers/embedding/voyage_client.py` + `src/flashback/workers/embedding/worker.py` (batch, sync)
- Modify: `src/flashback/workers/extraction/voyage_query.py` (sync refinement embedder)
- Test: `tests/usage/test_voyage_usage.py`

**Interfaces:**
- Consumes: `record_llm_usage` (async) for `retrieval/voyage.py`; `record_llm_usage_sync` for the two sync sites.
- Produces: each Voyage call records a `usage_events` row with `feature='embedding_query'` (query embedders) or `feature='embedding_row'` (batch worker), `provider='voyage'`, and `input_tokens = result.total_tokens`.

- [ ] **Step 1: Write the failing test**

`tests/usage/test_voyage_usage.py`:

```python
from types import SimpleNamespace

import pytest


def test_sync_query_embedder_records_embedding_query(monkeypatch):
    from flashback.workers.extraction import voyage_query
    calls = {}
    monkeypatch.setattr(
        voyage_query.usage_recorder, "record_llm_usage_sync",
        lambda **kw: calls.update(kw),
    )
    embedder = voyage_query.SyncVoyageQueryEmbedder(api_key="x", model="voyage-3-large")
    monkeypatch.setattr(embedder, "_get_client", lambda: SimpleNamespace(
        embed=lambda *a, **k: SimpleNamespace(
            embeddings=[[0.0] * 1024], total_tokens=42)))
    vec = embedder.embed("hello")
    assert vec is not None
    assert calls["feature"] == "embedding_query"
    assert calls["provider"] == "voyage"
    assert calls["input_tokens"] == 42
    assert calls["output_tokens"] == 0
```

> Adjust the constructor call to `SyncVoyageQueryEmbedder`'s real signature (read `voyage_query.py`). The invariant is: a successful embed records `embedding_query` / `voyage` / `total_tokens`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/usage/test_voyage_usage.py -v`
Expected: FAIL — `voyage_query.usage_recorder` undefined.

- [ ] **Step 3: Instrument the sync refinement embedder**

In `src/flashback/workers/extraction/voyage_query.py`, add `from flashback.usage import recorder as usage_recorder`. In `SyncVoyageQueryEmbedder.embed`, after `result = self._get_client().embed(...)` and before returning the vector, add:

```python
        usage_recorder.record_llm_usage_sync(
            feature="embedding_query", provider="voyage", model=self.model,
            input_tokens=int(getattr(result, "total_tokens", 0) or 0),
            output_tokens=0,
        )
```

- [ ] **Step 4: Instrument the async query embedder**

In `src/flashback/retrieval/voyage.py`, add `from flashback.usage import recorder as usage_recorder`. `_embed_sync` currently returns only the vector; change it to also surface `total_tokens` (return a tuple `(vector, total_tokens)`), and in the async `embed` method record after the successful embed:

```python
        vector, total_tokens = await asyncio.to_thread(self._embed_sync, query)
        await usage_recorder.record_llm_usage(
            feature="embedding_query", provider="voyage", model=self._model,
            input_tokens=int(total_tokens or 0), output_tokens=0,
        )
        return vector
```

Update `_embed_sync` to `return list(result.embeddings[0]), getattr(result, "total_tokens", 0)`. Keep the existing timeout/exception swallow behavior (return `None` on failure — in that path do not record).

- [ ] **Step 5: Instrument the batch embedding worker**

In `src/flashback/workers/embedding/voyage_client.py`, have `embed_batch` also return the batch `total_tokens` (return `(vectors, total_tokens)`), or expose it on the client. In `src/flashback/workers/embedding/worker.py` at the call site (~192), after a successful `embed_batch`, add `from flashback.usage import recorder as usage_recorder` at module top and:

```python
        usage_recorder.record_llm_usage_sync(
            feature="embedding_row", provider="voyage", model=model,
            input_tokens=int(total_tokens or 0), output_tokens=0,
        )
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/usage/test_voyage_usage.py tests/retrieval tests/workers/embedding -v`
Expected: PASS / no new failures.

- [ ] **Step 7: Commit**

```bash
git add src/flashback/retrieval/voyage.py src/flashback/workers/embedding/voyage_client.py src/flashback/workers/embedding/worker.py src/flashback/workers/extraction/voyage_query.py tests/usage/test_voyage_usage.py
git commit -m "feat(usage): meter Voyage embedding token usage (3 sites)"
```

---

### Task 8: `POST /usage/events` endpoint

**Files:**
- Create: `src/flashback/http/routes/usage.py`
- Modify: `src/flashback/http/app.py` (import + register router)
- Test: `tests/http/test_usage_route.py`

**Interfaces:**
- Consumes: `flashback.usage.recorder.insert_event`; the app's async DB pool via `Depends(get_db_pool)`.
- Produces: `POST /usage/events` accepting `UsageEventRequest` (pydantic), forcing `service='node'`, inserting one row, returning `{"id": "<uuid>"}` with 201. Malformed body → 422. No auth dependency (Node is the auth boundary).

Request model fields: `feature: str`, `provider: str`, `model: str`, `cost_usd: float`, `unit_type: str = "tokens"`, `units: float | None = None`, `input_tokens: int | None = None`, `output_tokens: int | None = None`, `cache_read_tokens: int | None = None`, `cache_write_tokens: int | None = None`, `person_id: str | None = None`, `session_id: str | None = None`.

- [ ] **Step 1: Write the failing test**

`tests/http/test_usage_route.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_post_usage_events_inserts_node_row(client_with_db, async_db_pool):
    resp = await client_with_db.post("/usage/events", json={
        "feature": "artifact_image", "provider": "gemini", "model": "img-1",
        "units": 1, "unit_type": "images", "cost_usd": 0.04,
    })
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    assert new_id

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT service, feature, unit_type, cost_usd FROM usage_events "
                "WHERE id = %s", (new_id,))
            row = await cur.fetchone()
    assert row[0] == "node"           # forced server-side
    assert row[1] == "artifact_image"
    assert row[2] == "images"
    assert float(row[3]) == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_post_usage_events_rejects_missing_required_field(client_with_db):
    resp = await client_with_db.post("/usage/events", json={
        "feature": "artifact_image", "provider": "gemini",  # missing model + cost_usd
    })
    assert resp.status_code == 422
```

> Use the same `client_with_db` / `async_db_pool` fixtures the other HTTP DB tests use (`tests/http/conftest.py`). Adjust the client call style (`await client.post` vs `client.post`) to match those tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/http/test_usage_route.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Write the route**

`src/flashback/http/routes/usage.py`:

```python
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from flashback.http.deps import get_db_pool
from flashback.usage.queries import INSERT_USAGE_EVENT

log = structlog.get_logger("flashback.http.usage")

router = APIRouter(prefix="/usage")


class UsageEventRequest(BaseModel):
    feature: str
    provider: str
    model: str
    cost_usd: float
    unit_type: str = "tokens"
    units: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    person_id: str | None = None
    session_id: str | None = None


class UsageEventResponse(BaseModel):
    id: str


@router.post("/events", response_model=UsageEventResponse,
             status_code=status.HTTP_201_CREATED)
async def create_usage_event(
    body: UsageEventRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> UsageEventResponse:
    if db_pool is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="database unavailable")
    row = {
        "service": "node",  # forced: Node cannot post agent-attributed rows
        "feature": body.feature,
        "provider": body.provider,
        "model": body.model,
        "input_tokens": body.input_tokens,
        "output_tokens": body.output_tokens,
        "cache_read_tokens": body.cache_read_tokens,
        "cache_write_tokens": body.cache_write_tokens,
        "units": body.units,
        "unit_type": body.unit_type,
        "cost_usd": body.cost_usd,
        "person_id": body.person_id,
        "session_id": body.session_id,
    }
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(INSERT_USAGE_EVENT, row)
            new_id = (await cur.fetchone())[0]
    return UsageEventResponse(id=new_id)
```

> Confirm the pool type import (`psycopg_pool.AsyncConnectionPool`) matches what `get_db_pool` in `src/flashback/http/deps.py` returns; mirror the annotation used by `profile_facts.py`.

- [ ] **Step 4: Register the router in `app.py`**

In `src/flashback/http/app.py`, add to the route imports block (~lines 31-46):

```python
from flashback.http.routes.usage import router as usage_router
```

and in `create_app` alongside the other `app.include_router(...)` calls (~lines 259-274):

```python
    app.include_router(usage_router)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/http/test_usage_route.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/http/routes/usage.py src/flashback/http/app.py tests/http/test_usage_route.py
git commit -m "feat(usage): POST /usage/events endpoint (Node artifact cost)"
```

---

### Task 9: Node handoff doc

**Files:**
- Create: `docs/OBSERVABILITY_DASHBOARD_NODE_PROMPT.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Write the doc**

Create `docs/OBSERVABILITY_DASHBOARD_NODE_PROMPT.md` covering, for the Node repo:

1. **`POST /usage/events`** — call it after each image/video/voice generation. Document the request body (fields from Task 8), that `service` is forced to `node`, and that Node computes its own `cost_usd` from its own pricing. Give a `curl` example and note it is unauthenticated (Node is the auth boundary, service token + private network).
2. **Read views** to power the dashboard: `dashboard_cost_by_feature`, `dashboard_cost_by_model`, `dashboard_storybooks`, `dashboard_tributes`, `dashboard_content_counts`, `dashboard_worker_health`. Note the cost views are raw aggregates + `usage_events.created_at`, so Node windows ("today / this week / all time") in its own query.
3. **Suggested `GET /dashboard` API shape** Node exposes to the dashboard UI repo: `{ cost: { by_feature, by_model, total }, ops: { legacies, storybooks, tributes, content, workers } }`, windowed by a query param.
4. **Session counts** (started/completed) are Node's to add from its own session store (DynamoDB); they are not in the agent Postgres.
5. **Feature taxonomy** table (from Task 6) so Node's cost-by-feature labels line up with the agent's.

- [ ] **Step 2: Commit**

```bash
git add docs/OBSERVABILITY_DASHBOARD_NODE_PROMPT.md
git commit -m "docs: Node integration prompt for observability dashboard"
```

---

## Self-Review

**Spec coverage:**
- §2 boundaries (agent sole writer, `service='node'` forced) → Tasks 4, 8. ✅
- §3 `usage_events` table + decisions → Task 1. ✅
- §4.1 Python metering (interface chokepoint, streaming, Voyage, pricing-in-code, non-blocking, never-break) → Tasks 2, 3, 5, 6, 7. The spec's "background task on hot path" is refined to an `asyncio.to_thread` sync insert — non-blocking to the event loop, correct across worker loops, and swallow-on-failure preserves "never break the turn." ✅
- §4.2 `POST /usage/events` → Task 8. ✅
- §5 feature taxonomy → Task 6 table (spec's `rolling_summary` dropped, with rationale — single LLM call can't be split). ✅
- §6 `dashboard_*` views → Task 1. ✅
- §7 Node handoff doc → Task 9. ✅
- §8 out-of-scope (per-legacy, trends, queue depth, infra) → not built; `person_id`/`created_at` captured for later. ✅
- §9 testing → each build task ends with a test. ✅

**Placeholder scan:** No "TBD/TODO". The only marked-uncertain values are the OpenAI/Voyage pricing numbers, flagged `# VERIFY` — they are operator-maintained data, and tests use explicit test rates so correctness does not depend on them.

**Type consistency:** `record_llm_usage` / `record_llm_usage_sync` / `insert_event` / `compute_cost` / `usage_from_anthropic` / `usage_from_openai` / `INSERT_USAGE_EVENT` names are used identically across tasks. The `feature` kwarg added in Task 5 is consumed by Tasks 6 and 7. Row-dict keys match the SQL named params in Task 4.
