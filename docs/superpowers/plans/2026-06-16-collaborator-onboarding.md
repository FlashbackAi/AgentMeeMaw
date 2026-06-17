# Collaborator Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A collaborator joining an established legacy is tracked in a new `collaborator_onboarding` table (mirrored from Node's `session_metadata`), gets an opener grounded in their relationship to the subject ("As his daughter, …"), and — when one contributor recalls another's moment — is credited by name **and** relationship ("Ravi, her brother, told us…").

**Architecture:** Node owns authoritative membership (DynamoDB) and passes `role`/`voice_anchor_text`/modal timestamps in `session_metadata`. A session-start step mirrors those into `collaborator_onboarding` (agent Postgres) and into `state.session_metadata['contributor_voice_anchor']` for the opener. Relationship-aware attribution reads the table via a LEFT JOIN in moment search. Builds on sub-project 1 (`told_by_user_id`) and sub-project 2 (per-contributor opener scoping + name-only attribution).

**Tech Stack:** Python, FastAPI + pydantic v2, psycopg (raw SQL) + pgvector, Valkey, SQS. Tests: pytest via `.venv/Scripts/python.exe`.

**Spec:** `docs/superpowers/specs/2026-06-16-collaborator-onboarding-design.md`

---

## Execution notes (READ FIRST)

- **NO COMMITS this cycle.** No `git commit`/`git add` in any task. Changes accumulate in the working tree on `feature/collaborator-provenance`; the user commits later. Each task ends with a verification step (tests + `git diff --stat -- <task files>`) instead of a commit.
- **Test command:** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings <path>`. Never plain `pytest`.
- **DB-gated tests need the test DB up.** Export it: `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test` (Docker Postgres must be running). `conftest` drops + re-applies all migrations at session start. If `TEST_DATABASE_URL` is unset, DB-gated tests skip — that's fine; the no-DB tests carry most coverage.
- **Baseline:** full suite is **14 failures no-DB / 28 with-DB** (pre-existing environment failures). The bar is: add zero NEW failures. Judge by diffing the FAILED list, not expecting zero.
- **NEVER `git checkout`** (would lose uncommitted work). Verify branch with `git rev-parse --abbrev-ref HEAD`.
- Reviewers isolate a task's change with `git diff -- <task files>` (no per-task commit SHAs).
- **Migration order:** `0027` is taken (sub-project 2's `active_moments` recreation). This plan adds **0028** (collaborator_onboarding) and **0029** (other `active_*` views). `start-local.ps1` self-heals migration tracking; for tests, `conftest` applies all `*.up.sql` in order.

## File map

| File | Responsibility | Task |
|---|---|---|
| `migrations/0028_collaborator_onboarding.up.sql` / `.down.sql` | new table + view + indexes | 1 |
| `migrations/0029_expose_told_by_on_active_views.up.sql` / `.down.sql` | recreate 4 `active_*` views to expose `told_by_user_id` | 2 |
| `src/flashback/collaborator_onboarding/__init__.py`, `queries.py`, `repository.py` | upsert + voice-anchor read | 3 |
| `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py` | session-start mirror step | 4 |
| `src/flashback/orchestrator/orchestrator.py` | wire the step into session-start | 4 |
| `src/flashback/response_generator/schema.py` | `StarterContext.contributor_voice_anchor` | 5 |
| `src/flashback/orchestrator/steps/starter_opener.py` | thread voice anchor → `StarterContext` | 5 |
| `src/flashback/response_generator/prompts.py` | opener voice-anchor framing + recall relationship crediting | 5, 6 |
| `src/flashback/retrieval/queries.py`, `schema.py`, `service.py` | JOIN + `told_by_relationship` | 6 |
| `src/flashback/response_generator/context.py` | render relationship in attribution | 6 |

New test files: `tests/collaborator_onboarding/test_repository.py`, `tests/orchestrator/test_apply_collaborator_onboarding.py`, `tests/orchestrator/test_voice_anchor_opener.py`, `tests/retrieval/test_relationship_attribution.py`, plus extensions to `tests/response_generator/test_attribution_render.py`.

---

### Task 1: Migration 0028 — `collaborator_onboarding` table

**Files:**
- Create: `migrations/0028_collaborator_onboarding.up.sql`
- Create: `migrations/0028_collaborator_onboarding.down.sql`

No migration-runner test harness; verified by review + applying to the local DB. Mirrors the style of `0026`/`0027` (header block, `BEGIN;`/`COMMIT;`).

- [ ] **Step 1: Write the up migration**

`migrations/0028_collaborator_onboarding.up.sql`:

```sql
-- ============================================================================
-- 0028_collaborator_onboarding.up.sql
-- Collaborator feature Phase 1, sub-project 3: onboarding coverage signals.
-- ----------------------------------------------------------------------------
-- Agent-internal coverage signals for non-creator collaborators, keyed by
-- (person_id, user_id). DENORMALIZED MIRROR of the Node-side DynamoDB
-- membership row, which stays the source of truth for membership identity,
-- raw modal answers, and onboarding_complete. Fields are mirrored here at
-- session start (apply_collaborator_onboarding) so per-turn reads are local
-- single-row queries instead of cross-service lookups.
--
-- Columns for the deferred nudge / first-moment / removal flows are present
-- but unused this cycle (filled by later sub-projects). One active row per
-- (person_id, user_id).
-- ============================================================================

BEGIN;

CREATE TABLE collaborator_onboarding (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,

    -- Voice anchor — the contributor's relationship to the subject
    -- ("his daughter"). Used as the opener prior and the attribution
    -- relationship phrase. Filled from the modal answer (mirrored from
    -- DynamoDB at session start) or, later, from a tap-card answer.
    voice_anchor_text TEXT,
    voice_anchored_at TIMESTAMPTZ,

    -- First-moment marker — flipped by the extraction worker when the first
    -- moment with told_by_user_id = this user_id commits. DEFERRED this cycle.
    first_moment_id UUID REFERENCES moments(id),
    first_moment_recorded_at TIMESTAMPTZ,

    -- Modal state mirror — denormalized from DynamoDB at session start.
    modal_answered_at TIMESTAMPTZ,
    modal_dismissed_at TIMESTAMPTZ,

    -- Agent-internal tap counter for the deferred 3-nudge cap. DEFERRED.
    taps_emitted INT NOT NULL DEFAULT 0,

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'removed')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        (voice_anchored_at IS NULL AND voice_anchor_text IS NULL)
        OR (voice_anchored_at IS NOT NULL AND voice_anchor_text IS NOT NULL)
    ),
    CHECK (
        (first_moment_id IS NULL AND first_moment_recorded_at IS NULL)
        OR (first_moment_id IS NOT NULL AND first_moment_recorded_at IS NOT NULL)
    )
);

CREATE TRIGGER trg_collaborator_onboarding_updated_at
    BEFORE UPDATE ON collaborator_onboarding
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- One active onboarding row per contributor per memorial. A removed row
-- stays for audit; a re-invite gets a NEW active row.
CREATE UNIQUE INDEX uq_collaborator_onboarding_active
    ON collaborator_onboarding (person_id, user_id)
    WHERE status = 'active';

CREATE INDEX idx_collaborator_onboarding_person_user
    ON collaborator_onboarding (person_id, user_id, status);

CREATE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';

COMMIT;
```

- [ ] **Step 2: Write the down migration**

`migrations/0028_collaborator_onboarding.down.sql`:

```sql
-- ============================================================================
-- 0028_collaborator_onboarding.down.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_collaborator_onboarding;
DROP TABLE IF EXISTS collaborator_onboarding;

COMMIT;
```

- [ ] **Step 3: Self-check the SQL**

Confirm: `gen_random_uuid()` (pgcrypto) and `trg_set_updated_at()` exist (used by other tables in `0001`); `persons(id)` and `moments(id)` exist as FKs; the partial unique index is `WHERE status='active'`; the down drops view then table. (Optional, if Docker is up: `export TEST_DATABASE_URL=…` and run `tests/collaborator_onboarding` in Task 3 — `conftest` applies this migration.)

- [ ] **Step 4: Verify (NO COMMIT)**

Run: `git status --short -- migrations/` — confirm only the two new 0028 files. Do NOT commit.

---

### Task 2: Migration 0029 — expose `told_by_user_id` on the other `active_*` views

**Files:**
- Create: `migrations/0029_expose_told_by_on_active_views.up.sql`
- Create: `migrations/0029_expose_told_by_on_active_views.down.sql`

`active_entities` / `active_traits` / `active_questions` / `active_profile_facts` were created `SELECT *` before `0026`, so they froze without `told_by_user_id`. Recreating them `SELECT *` now re-freezes at the current schema (which has the column). Confirmed (grep) that no other view/object depends on these four, so no CASCADE recreation is needed.

- [ ] **Step 1: Write the up migration**

`migrations/0029_expose_told_by_on_active_views.up.sql`:

```sql
-- ============================================================================
-- 0029_expose_told_by_on_active_views.up.sql
-- Collaborator feature Phase 1, sub-project 3 (companion).
-- ----------------------------------------------------------------------------
-- active_entities / active_traits / active_questions / active_profile_facts
-- were created as SELECT * before told_by_user_id existed (0026); Postgres
-- freezes SELECT* column lists at view-creation time, so the column never
-- appeared in these views. Recreate them as SELECT * now to re-freeze at the
-- current schema (which includes told_by_user_id). active_moments was already
-- handled in 0027.
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_entities;
CREATE VIEW active_entities AS SELECT * FROM entities WHERE status = 'active';

DROP VIEW IF EXISTS active_traits;
CREATE VIEW active_traits AS SELECT * FROM traits WHERE status = 'active';

DROP VIEW IF EXISTS active_questions;
CREATE VIEW active_questions AS SELECT * FROM questions WHERE status = 'active';

DROP VIEW IF EXISTS active_profile_facts;
CREATE VIEW active_profile_facts AS SELECT * FROM profile_facts WHERE status = 'active';

COMMIT;
```

- [ ] **Step 2: Write the down migration**

`migrations/0029_expose_told_by_on_active_views.down.sql` (recreating `SELECT *` is symmetric — the view is `SELECT *` either way; this just guarantees rollback doesn't error):

```sql
-- ============================================================================
-- 0029_expose_told_by_on_active_views.down.sql
-- Recreates the four views as SELECT * (functionally identical; the pre-0029
-- frozen column set cannot be reconstructed and need not be).
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_entities;
CREATE VIEW active_entities AS SELECT * FROM entities WHERE status = 'active';

DROP VIEW IF EXISTS active_traits;
CREATE VIEW active_traits AS SELECT * FROM traits WHERE status = 'active';

DROP VIEW IF EXISTS active_questions;
CREATE VIEW active_questions AS SELECT * FROM questions WHERE status = 'active';

DROP VIEW IF EXISTS active_profile_facts;
CREATE VIEW active_profile_facts AS SELECT * FROM profile_facts WHERE status = 'active';

COMMIT;
```

- [ ] **Step 3: Self-check**

Confirm the four base tables (`entities`, `traits`, `questions`, `profile_facts`) all have a `status` column (they do — `0001`/`0010`) and `told_by_user_id` (added in `0026`). No CASCADE needed (no dependents).

- [ ] **Step 4: Verify (NO COMMIT)**

`git status --short -- migrations/` shows the two new 0029 files.

---

### Task 3: `collaborator_onboarding` module (repository + queries)

**Files:**
- Create: `src/flashback/collaborator_onboarding/__init__.py`
- Create: `src/flashback/collaborator_onboarding/queries.py`
- Create: `src/flashback/collaborator_onboarding/repository.py`
- Test: `tests/collaborator_onboarding/__init__.py` (empty), `tests/collaborator_onboarding/test_repository.py`

The repository exposes two async functions used by the apply step (write) and retrieval/opener (read). DB-gated.

- [ ] **Step 1: Write the SQL constants**

`src/flashback/collaborator_onboarding/queries.py`:

```python
"""Literal SQL for the collaborator_onboarding mirror table."""

# Upsert the active onboarding row for (person_id, user_id). On conflict
# (the partial-unique active row), refresh modal timestamps and ONLY set the
# voice anchor when a new non-NULL one is supplied (never clobber a captured
# anchor with an empty re-mirror). COALESCE keeps the existing value.
UPSERT_ONBOARDING_SQL = """
INSERT INTO collaborator_onboarding (
    person_id, user_id,
    voice_anchor_text, voice_anchored_at,
    modal_answered_at, modal_dismissed_at
)
VALUES (
    %(person_id)s, %(user_id)s,
    %(voice_anchor_text)s, %(voice_anchored_at)s,
    %(modal_answered_at)s, %(modal_dismissed_at)s
)
ON CONFLICT (person_id, user_id) WHERE status = 'active'
DO UPDATE SET
    voice_anchor_text = COALESCE(EXCLUDED.voice_anchor_text, collaborator_onboarding.voice_anchor_text),
    voice_anchored_at = COALESCE(EXCLUDED.voice_anchored_at, collaborator_onboarding.voice_anchored_at),
    modal_answered_at = COALESCE(EXCLUDED.modal_answered_at, collaborator_onboarding.modal_answered_at),
    modal_dismissed_at = COALESCE(EXCLUDED.modal_dismissed_at, collaborator_onboarding.modal_dismissed_at)
"""

GET_VOICE_ANCHOR_SQL = """
SELECT voice_anchor_text
FROM collaborator_onboarding
WHERE person_id = %(person_id)s
  AND user_id = %(user_id)s
  AND status = 'active'
"""
```

> Implementer note: the partial-unique index is `(person_id, user_id) WHERE status='active'`. Postgres `ON CONFLICT` with a partial index requires the matching `WHERE` predicate in the conflict target — included above. The CHECK constraint requires `voice_anchor_text` and `voice_anchored_at` to be both NULL or both set; the apply step (Task 4) guarantees it passes both or neither.

- [ ] **Step 2: Write the repository**

`src/flashback/collaborator_onboarding/repository.py`:

```python
"""Async read/write helpers for the collaborator_onboarding mirror table."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from flashback.collaborator_onboarding.queries import (
    GET_VOICE_ANCHOR_SQL,
    UPSERT_ONBOARDING_SQL,
)


async def upsert_onboarding(
    conn,
    *,
    person_id: UUID,
    user_id: UUID,
    voice_anchor_text: str | None = None,
    voice_anchored_at: datetime | None = None,
    modal_answered_at: datetime | None = None,
    modal_dismissed_at: datetime | None = None,
) -> None:
    """Upsert the active onboarding row, mirroring Node session_metadata.

    Never clobbers an existing voice anchor with NULL (COALESCE in SQL).
    Caller (apply step) must pass voice_anchor_text and voice_anchored_at
    together or both None to satisfy the table CHECK.
    """
    await conn.execute(
        UPSERT_ONBOARDING_SQL,
        {
            "person_id": person_id,
            "user_id": user_id,
            "voice_anchor_text": voice_anchor_text,
            "voice_anchored_at": voice_anchored_at,
            "modal_answered_at": modal_answered_at,
            "modal_dismissed_at": modal_dismissed_at,
        },
    )


async def get_voice_anchor(conn, *, person_id: UUID, user_id: UUID) -> str | None:
    """Return the active row's voice_anchor_text, or None."""
    cur = await conn.execute(
        GET_VOICE_ANCHOR_SQL, {"person_id": person_id, "user_id": user_id}
    )
    row = await cur.fetchone()
    return row[0] if row else None
```

`src/flashback/collaborator_onboarding/__init__.py`:

```python
"""Collaborator onboarding coverage-signal mirror (sub-project 3)."""

from flashback.collaborator_onboarding.repository import (
    get_voice_anchor,
    upsert_onboarding,
)

__all__ = ["get_voice_anchor", "upsert_onboarding"]
```

- [ ] **Step 3: Write the failing DB-gated test**

`tests/collaborator_onboarding/__init__.py`: empty file.

`tests/collaborator_onboarding/test_repository.py`:

```python
"""collaborator_onboarding repository (DB-gated)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.collaborator_onboarding import get_voice_anchor, upsert_onboarding
from flashback.db.connection import make_async_pool

_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


async def _person(pool) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO persons (name) VALUES (%s) RETURNING id", ("Subj",)
        )
        pid = (await cur.fetchone())[0]
        await conn.commit()
    return pid


@pytest.fixture
async def pool(schema_applied: str):
    p = make_async_pool(schema_applied, min_size=1, max_size=2)
    await p.open()
    try:
        yield p
    finally:
        async with p.connection() as conn:
            await conn.execute("DELETE FROM collaborator_onboarding")
            await conn.execute("DELETE FROM persons")
            await conn.commit()
        await p.close()


async def test_upsert_creates_then_reads_voice_anchor(pool):
    pid = await _person(pool)
    uid = uuid4()
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=uid,
            voice_anchor_text="his daughter", voice_anchored_at=now,
            modal_answered_at=now,
        )
        await conn.commit()
        assert await get_voice_anchor(conn, person_id=pid, user_id=uid) == "his daughter"


async def test_reupsert_does_not_clobber_anchor_with_null(pool):
    pid = await _person(pool)
    uid = uuid4()
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=uid,
            voice_anchor_text="his daughter", voice_anchored_at=now,
        )
        await conn.commit()
        # Re-mirror with no anchor (e.g. a later session without the modal)
        await upsert_onboarding(
            conn, person_id=pid, user_id=uid, modal_dismissed_at=now,
        )
        await conn.commit()
        assert await get_voice_anchor(conn, person_id=pid, user_id=uid) == "his daughter"


async def test_get_voice_anchor_none_when_absent(pool):
    pid = await _person(pool)
    async with pool.connection() as conn:
        assert await get_voice_anchor(conn, person_id=pid, user_id=uuid4()) is None
```

> Implementer note: confirm the `schema_applied` session fixture and `make_async_pool` import path against `tests/conftest.py` and `tests/retrieval/conftest.py` (the retrieval conftest shows the async-pool pattern). Adapt the fixture to match the repo's style if `make_async_pool` differs.

- [ ] **Step 4: Run — fail then pass**

Run: `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test && .venv/Scripts/python.exe -m pytest tests/collaborator_onboarding -q --tb=short -p no:warnings`
Expected first: import error / table missing → after Steps 1-2 migrations + this code, 3 pass. If Docker is down, tests skip — note that and rely on review.

- [ ] **Step 5: Verify (NO COMMIT)**

`git status --short -- src/flashback/collaborator_onboarding tests/collaborator_onboarding`

---

### Task 4: `apply_collaborator_onboarding` orchestrator step + wiring

**Files:**
- Create: `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py`
- Modify: `src/flashback/orchestrator/orchestrator.py` (session-start pipeline + import)
- Test: `tests/orchestrator/test_apply_collaborator_onboarding.py`

The step runs after `load_person`, before the opener. It mirrors `session_metadata` into the table and writes `state.session_metadata['contributor_voice_anchor']` for the opener. Creator (no `role='collaborator'` / no `user_id`) is a no-op.

- [ ] **Step 1: Write the step**

`src/flashback/orchestrator/steps/apply_collaborator_onboarding.py`:

```python
"""Mirror Node-supplied collaborator onboarding signals at session start."""

from __future__ import annotations

from datetime import datetime

import structlog

from flashback.collaborator_onboarding import upsert_onboarding
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState

log = structlog.get_logger("flashback.orchestrator")


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp from session_metadata (handles trailing Z)."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def apply_collaborator_onboarding(
    state: SessionStartState, deps: OrchestratorDeps
) -> None:
    """If this is a collaborator session, mirror onboarding signals.

    Upserts the collaborator_onboarding row from session_metadata and stamps
    the resolved voice anchor into state.session_metadata so the opener can
    use it. No-op for the creator (no role='collaborator' or no user_id).
    """
    with timed_step(log, "apply_collaborator_onboarding"):
        meta = state.session_metadata or {}
        if meta.get("role") != "collaborator" or state.user_id is None:
            return

        voice_anchor_text = (meta.get("voice_anchor_text") or "").strip() or None
        voice_anchored_at = _parse_ts(meta.get("voice_anchored_at"))
        # The table CHECK requires both-or-neither; if we have text but no
        # timestamp (or vice versa), pass both or neither.
        if voice_anchor_text and voice_anchored_at is None:
            voice_anchored_at = state.started_at
        if voice_anchored_at is not None and not voice_anchor_text:
            voice_anchored_at = None

        try:
            async with deps.db_pool.connection() as conn:
                await upsert_onboarding(
                    conn,
                    person_id=state.person_id,
                    user_id=state.user_id,
                    voice_anchor_text=voice_anchor_text,
                    voice_anchored_at=voice_anchored_at,
                    modal_answered_at=_parse_ts(meta.get("modal_answered_at")),
                    modal_dismissed_at=_parse_ts(meta.get("modal_dismissed_at")),
                )
                await conn.commit()
        except Exception as exc:  # noqa: BLE001 - onboarding must not break session start
            log.warning(
                "apply_collaborator_onboarding.degraded",
                error=type(exc).__name__,
                detail=str(exc),
            )
            return

        if voice_anchor_text:
            state.session_metadata["contributor_voice_anchor"] = voice_anchor_text
            log.info("collaborator_onboarding.voice_anchor_set")
```

- [ ] **Step 2: Wire it into the session-start pipeline**

In `src/flashback/orchestrator/orchestrator.py`, add to the steps import block (near `apply_theme_unlock`, ~line 44):

```python
    apply_collaborator_onboarding,
```

(i.e. the `from flashback.orchestrator.steps import (...)` list — confirm the exact import form; the file imports step functions by name.)

Then in `handle_session_start`, insert a new `execute(...)` call **after the `apply_theme_unlock` block (ends ~line 134) and before `if self._deps.response_generator is not None:`**:

```python
            await execute(
                policies=SESSION_START_POLICIES,
                step_name="apply_collaborator_onboarding",
                fn=lambda: apply_collaborator_onboarding(state, self._deps),
                state=state,
            )
```

> Implementer note: there are multiple session-start entry points in this file (the streaming twin etc. — `grep -n "step_name=\"apply_theme_unlock\"" orchestrator.py`). Add the same `apply_collaborator_onboarding` execute call after EACH `apply_theme_unlock` call so both JSON and streaming session-start paths mirror onboarding. Use the same `SESSION_START_POLICIES` and `state`/`self._deps` in scope at each site.

- [ ] **Step 3: Write the failing test**

`tests/orchestrator/test_apply_collaborator_onboarding.py`:

```python
"""apply_collaborator_onboarding mirror step (sub-project 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.orchestrator.state import SessionStartState
from flashback.orchestrator.steps.apply_collaborator_onboarding import (
    apply_collaborator_onboarding,
)


class _FakeConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


class _Deps:
    def __init__(self, pool):
        self.db_pool = pool


def _state(user_id, meta):
    return SessionStartState(
        session_id=uuid4(),
        person_id=uuid4(),
        user_id=user_id,
        session_metadata=meta,
        started_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_collaborator_upserts_and_sets_voice_anchor():
    conn = _FakeConn()
    state = _state(
        uuid4(),
        {"role": "collaborator", "voice_anchor_text": "his daughter"},
    )
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    assert conn.calls, "expected an upsert"
    assert state.session_metadata["contributor_voice_anchor"] == "his daughter"


@pytest.mark.asyncio
async def test_creator_is_noop():
    conn = _FakeConn()
    state = _state(None, {})  # no role, no user_id
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    assert conn.calls == []
    assert "contributor_voice_anchor" not in state.session_metadata


@pytest.mark.asyncio
async def test_collaborator_without_anchor_upserts_no_wm_signal():
    conn = _FakeConn()
    state = _state(uuid4(), {"role": "collaborator", "modal_dismissed_at": "2026-06-16T04:06:10Z"})
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    assert conn.calls, "expected an upsert even without an anchor"
    assert "contributor_voice_anchor" not in state.session_metadata
```

> Implementer note: confirm `SessionStartState`'s required fields/order against `src/flashback/orchestrator/state.py` and adapt the `_state` constructor. Confirm `deps.db_pool.connection()` is an async context manager (the fake mimics that). If the real pool's `.connection()` returns an awaitable that yields a context manager, adjust the fake to match.

- [ ] **Step 4: Run — fail then pass**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_apply_collaborator_onboarding.py -q --tb=short -p no:warnings`
Expected: 3 pass after the step exists.

- [ ] **Step 5: Run the orchestrator dir**

Run (with `TEST_DATABASE_URL`): `.venv/Scripts/python.exe -m pytest tests/orchestrator -q --tb=no -p no:warnings 2>&1 | tail -8`
Expected: new tests pass; only the known baseline orchestrator failures remain; no NEW failures.

- [ ] **Step 6: Verify (NO COMMIT)**

`git diff --stat -- src/flashback/orchestrator/orchestrator.py src/flashback/orchestrator/steps/apply_collaborator_onboarding.py`

---

### Task 5: Voice-anchor opener

**Files:**
- Modify: `src/flashback/response_generator/schema.py` (`StarterContext`)
- Modify: `src/flashback/orchestrator/steps/starter_opener.py` (`build_starter_context`)
- Modify: `src/flashback/response_generator/prompts.py` (`STARTER_OPENER_PROMPT`)
- Test: `tests/orchestrator/test_voice_anchor_opener.py`; extend `tests/response_generator/test_prompts.py` style check

- [ ] **Step 1: Write the failing tests**

`tests/orchestrator/test_voice_anchor_opener.py`:

```python
"""Voice anchor flows into the StarterContext + rendered opener (sub-project 3)."""

from flashback.orchestrator.steps.starter_opener import build_starter_context
from flashback.orchestrator.state import SessionStartState
from flashback.response_generator.context import render_starter_context
from datetime import datetime, timezone
from uuid import uuid4


def _state(meta):
    s = SessionStartState(
        session_id=uuid4(),
        person_id=uuid4(),
        user_id=uuid4(),
        session_metadata=meta,
        started_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
    )
    s.person_name = "LegacyTest1"
    s.person_relationship = None
    s.person_gender = "he"
    return s


def test_voice_anchor_surfaces_in_starter_context_and_render():
    state = _state({"contributor_voice_anchor": "his daughter"})
    ctx = build_starter_context(state)
    assert ctx.contributor_voice_anchor == "his daughter"
    rendered = render_starter_context(ctx)
    assert "his daughter" in rendered


def test_no_voice_anchor_is_none():
    ctx = build_starter_context(_state({}))
    assert ctx.contributor_voice_anchor is None
```

`tests/response_generator/test_prompts.py` — add:

```python
def test_starter_prompt_has_voice_anchor_instruction():
    from flashback.response_generator.prompts import STARTER_OPENER_PROMPT
    p = STARTER_OPENER_PROMPT.lower()
    assert "voice_anchor" in p or "relationship to the subject" in p
```

- [ ] **Step 2: Run — confirm fail**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_voice_anchor_opener.py tests/response_generator/test_prompts.py::test_starter_prompt_has_voice_anchor_instruction -q --tb=short -p no:warnings`
Expected: FAIL (`StarterContext` has no `contributor_voice_anchor`; prompt lacks the instruction).

- [ ] **Step 3: Add the field to `StarterContext`**

In `src/flashback/response_generator/schema.py`, in `StarterContext` (after `contributor_role`, before `anchor_question_text`):

```python
    # Collaborator's relationship to the subject (sub-project 3), e.g.
    # "his daughter". When present, the opener grounds in it. None for the
    # creator / contributors without a captured voice anchor.
    contributor_voice_anchor: str | None = None
```

- [ ] **Step 4: Thread it in `build_starter_context`**

In `src/flashback/orchestrator/steps/starter_opener.py`, in `build_starter_context`'s `return StarterContext(...)`, add (alongside `contributor_display_name=...`):

```python
        contributor_voice_anchor=_string_or_none(
            state.session_metadata.get("contributor_voice_anchor")
        ),
```

- [ ] **Step 5: Render it**

In `src/flashback/response_generator/context.py`, in `render_starter_context`, after the `contributor_name` block, add:

```python
    if ctx.contributor_voice_anchor:
        sections.append(
            _block("contributor_voice_anchor", xml_text(ctx.contributor_voice_anchor))
        )
```

- [ ] **Step 6: Add the prompt instruction**

In `src/flashback/response_generator/prompts.py`, in `STARTER_OPENER_PROMPT`, inside the "CONTINUITY" / hard-constraints area, add a paragraph (before the closing `"""`):

```
VOICE ANCHOR: If a <contributor_voice_anchor> block is provided, it is THIS
contributor's relationship to the subject (e.g. "his daughter"). Ground the
opener in it warmly ("As his daughter, what's a memory of him that's stayed
with you?"). Combined with the no-<prior_session_summary> rule above, this is
a first-time contributor — open fresh, do not imply you've spoken before.
```

- [ ] **Step 7: Run — confirm pass**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_voice_anchor_opener.py tests/response_generator -q --tb=short -p no:warnings 2>&1 | tail -5`
Expected: new tests pass; response_generator suite green.

- [ ] **Step 8: Verify (NO COMMIT)**

`git diff --stat -- src/flashback/response_generator/schema.py src/flashback/orchestrator/steps/starter_opener.py src/flashback/response_generator/context.py src/flashback/response_generator/prompts.py`

---

### Task 6: Relationship-aware attribution

**Files:**
- Modify: `src/flashback/retrieval/queries.py` (`SEARCH_MOMENTS_SQL`)
- Modify: `src/flashback/retrieval/schema.py` (`MomentResult`)
- Modify: `src/flashback/response_generator/context.py` (`render_turn_context` moments block)
- Modify: `src/flashback/response_generator/prompts.py` (`RECALL_PROMPT`)
- Test: `tests/retrieval/test_relationship_attribution.py`; extend `tests/response_generator/test_attribution_render.py`

`search_moments` LEFT JOINs the onboarding table so each moment carries the author's relationship; the renderer + prompt credit name **and** relationship.

- [ ] **Step 1: Add `told_by_relationship` to `MomentResult`**

In `src/flashback/retrieval/schema.py`, `MomentResult` (after `told_by_display_name`):

```python
    told_by_relationship: str | None = None
```

- [ ] **Step 2: JOIN in `SEARCH_MOMENTS_SQL`**

In `src/flashback/retrieval/queries.py`, `SEARCH_MOMENTS_SQL`: the outer `SELECT ... FROM candidates` gains the relationship via a LEFT JOIN. Replace the outer select so it reads (keep the CTE + the speaker-bias ORDER BY from sub-project 2 intact):

```python
SELECT
    candidates.id, candidates.person_id, candidates.title, candidates.narrative,
    candidates.time_anchor, candidates.life_period_estimate,
    candidates.sensory_details, candidates.emotional_tone,
    candidates.contributor_perspective, candidates.created_at,
    candidates.told_by_user_id, candidates.told_by_display_name,
    co.voice_anchor_text AS told_by_relationship,
    (candidates.narrative_embedding <=> %(query_vector)s) AS similarity_score
FROM   candidates
LEFT JOIN collaborator_onboarding co
       ON co.person_id = candidates.person_id
      AND co.user_id   = candidates.told_by_user_id
      AND co.status    = 'active'
ORDER  BY (candidates.narrative_embedding <=> %(query_vector)s)
          - CASE WHEN candidates.told_by_user_id = %(current_user_id)s
                 THEN %(speaker_bias)s ELSE 0 END
LIMIT  %(limit)s
"""
```

> Implementer note: READ the current `SEARCH_MOMENTS_SQL` first. The CTE `candidates` must already select `told_by_user_id`, `told_by_display_name`, `narrative_embedding`, `person_id` (it does, from sub-project 2). Qualify columns with `candidates.` once the JOIN is added (otherwise `id`/`person_id` are ambiguous). Keep `similarity_score` as the raw distance and the `speaker_bias` ORDER BY term exactly as-is.

- [ ] **Step 3: Render the relationship in attribution**

In `src/flashback/response_generator/context.py`, `render_turn_context`'s moments block — extend the existing cross-contributor `attribution` construction:

```python
            attribution = ""
            if (
                ctx.current_user_id is not None
                and moment.told_by_user_id is not None
                and moment.told_by_user_id != ctx.current_user_id
                and moment.told_by_display_name
            ):
                attribution = f' told_by="{xml_text(moment.told_by_display_name)}"'
                if moment.told_by_relationship:
                    attribution += f' relationship="{xml_text(moment.told_by_relationship)}"'
```

(Leave the rest of the line-building untouched.)

- [ ] **Step 4: Recall prompt — credit name + relationship**

In `src/flashback/response_generator/prompts.py`, in `RECALL_PROMPT`'s ATTRIBUTION paragraph (added in sub-project 2), append:

```
When a told_by moment also carries a relationship="..." attribute, credit
the contributor by name AND relationship naturally ("Ravi, her brother, told
us about..."). Use relationship only when present; otherwise name alone.
```

- [ ] **Step 5: Write the tests**

`tests/retrieval/test_relationship_attribution.py` (DB-gated):

```python
"""told_by_relationship flows from collaborator_onboarding into search (SP3)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.retrieval.conftest import insert_moment, insert_person, vector

_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@pytest.mark.asyncio
async def test_moment_carries_told_by_relationship(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    collab = uuid4()
    # onboarding row giving the collaborator a relationship
    async with async_db_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO collaborator_onboarding
                (person_id, user_id, voice_anchor_text, voice_anchored_at)
            VALUES (%s, %s, %s, %s)
            """,
            (person, collab, "her brother", datetime(2026, 6, 16, tzinfo=timezone.utc)),
        )
        await conn.commit()
    await insert_moment(
        async_db_pool, person, title="m1", embedding=vector(1.0, 0.0),
        told_by_user_id=collab,
    )
    results = await retrieval_service.search_moments("q", person, current_user_id=uuid4())
    assert results[0].told_by_relationship == "her brother"


@pytest.mark.asyncio
async def test_relationship_null_without_onboarding_row(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    await insert_moment(
        async_db_pool, person, title="m1", embedding=vector(1.0, 0.0),
        told_by_user_id=uuid4(),
    )
    results = await retrieval_service.search_moments("q", person, current_user_id=uuid4())
    assert results[0].told_by_relationship is None
```

`tests/response_generator/test_attribution_render.py` — add (uses the existing `_moment`/`_ctx` helpers; extend `_moment` if needed to accept `told_by_relationship`):

```python
def test_cross_contributor_moment_renders_relationship():
    me, other = uuid4(), uuid4()
    m = _moment(told_by_user_id=other, told_by_display_name="Ravi")
    m.told_by_relationship = "her brother"
    rendered = render_turn_context(_ctx(me, [m]))
    assert 'told_by="Ravi"' in rendered
    assert 'relationship="her brother"' in rendered


def test_recall_prompt_has_relationship_instruction():
    from flashback.response_generator.prompts import RECALL_PROMPT
    assert "relationship=" in RECALL_PROMPT
```

> Implementer note: the `_moment` helper in `test_attribution_render.py` builds a `MomentResult`; after Task 6 Step 1 it has `told_by_relationship` defaulting None, so setting it post-construction (as above) works. If `_moment` takes kwargs, pass `told_by_relationship="her brother"` instead.

- [ ] **Step 6: Run — fail then pass**

Run: `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test && .venv/Scripts/python.exe -m pytest tests/retrieval/test_relationship_attribution.py tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: all pass. Then the retrieval suite: `.venv/Scripts/python.exe -m pytest tests/retrieval -q --tb=no -p no:warnings 2>&1 | tail -3` — existing search_moments tests still pass (they don't set up onboarding rows → relationship NULL, harmless), only baseline `test_voyage` fails.

- [ ] **Step 7: Verify (NO COMMIT)**

`git diff --stat -- src/flashback/retrieval/queries.py src/flashback/retrieval/schema.py src/flashback/response_generator/context.py src/flashback/response_generator/prompts.py`

---

### Task 7: Verification sweep

- [ ] **Step 1: No-DB full suite**

Run: `unset TEST_DATABASE_URL; .venv/Scripts/python.exe -m pytest -q --tb=no -p no:warnings 2>&1 | grep -cE "^FAILED"`
Expected: `14` (no new no-DB failures).

- [ ] **Step 2: With-DB full suite**

Run: `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test && .venv/Scripts/python.exe -m pytest -q --tb=no -p no:warnings 2>&1 | grep -cE "^FAILED"`
Expected: `28` (the established with-DB baseline; new SP3 DB tests pass, zero new failures). If the count rose, stash the SP3 working-tree changes (`git stash push -u`), re-run to capture the true current baseline, `git stash pop`, and diff the FAILED lists — only NEW entries are regressions.

- [ ] **Step 3: Provenance read-path audit**

Run: `grep -rn "told_by_relationship\|collaborator_onboarding\|contributor_voice_anchor" src/flashback --include=*.py | grep -v __pycache__`
Expected: references only in the files this plan touched (retrieval, response_generator, orchestrator steps, collaborator_onboarding module). No stray references.

- [ ] **Step 4: Report (NO COMMIT)**

`git status --short` + `git diff --stat`. Summarize the full working-tree change set for the user to commit/push. Do NOT commit.

---

## Self-review (author checklist — completed)

**Spec coverage:**
- D1 collaborator_onboarding table → Task 1. ✓
- 0029 view exposure → Task 2. ✓
- D2 apply_collaborator_onboarding mirror + creator no-op + never-clobber → Tasks 3 (repo COALESCE) + 4 (step). ✓ (Corrected from spec: voice anchor goes into `state.session_metadata['contributor_voice_anchor']`, not WM — the pipeline reads opener context from state; WM is initialized after the opener.)
- D3 voice-anchor opener → Task 5. ✓
- D4 relationship attribution via JOIN + name-only fallback → Task 6. ✓
- D5 nudge/first-moment/removal deferred → no tasks (columns exist, unused). ✓

**Placeholder scan:** none — every step has concrete SQL/code/commands. The "implementer note" blocks flag where to confirm fixture/signature shapes against existing code (TurnState/SessionStartState fields, async-pool fixture, current SEARCH_MOMENTS_SQL) rather than reproduce uncertain harness internals.

**Type consistency:** `told_by_relationship` (MomentResult, render, test) consistent; `contributor_voice_anchor` (StarterContext, build_starter_context, render, session_metadata key) consistent; `upsert_onboarding`/`get_voice_anchor` signatures match across repository, step, and tests; `collaborator_onboarding` column names match between migration 0028, queries.py, and the JOIN in Task 6.
