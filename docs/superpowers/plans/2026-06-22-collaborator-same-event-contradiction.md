# Same-Event Linking + Contradiction Review (SP5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When two contributors describe the same event, auto-link their moments (notify + reversible unlink); when they contradict, persist a Node-reviewable item — both detected live on the existing per-moment compatibility search.

**Architecture:** Add a 4th compatibility verdict `same_event` to the extraction worker's existing refinement-candidate loop. A `same_event` verdict auto-writes a row to `moment_same_event_links`; a `contradiction` verdict writes a row to `moment_contradictions` (replacing today's log-only path). Both record tables store moment ids only — provenance (`told_by_*`) is resolved live via JOIN to `moments` at read time, so supersession (which changes the active row's teller) never staleness-poisons attribution. Supersession is extended to repoint these records. Same-event links feed `recall` retrieval into a `<linked_accounts>` block (cross-contributor attribution reuses the existing guard); contradictions are Node-only via new HTTP endpoints.

**Tech Stack:** Python, Postgres (psycopg, sync cursor in worker tx + async pool for reads/HTTP), FastAPI, pydantic, pytest (asyncio_mode=auto).

## Global Constraints

- **NO GIT COMMITS.** Standing user rule: never run `git commit`, `git add`, or `git checkout`. All work stays in the working tree on branch `feature/collaborator-provenance` for the user to commit. Every "Checkpoint" step below is a stop-and-verify, NOT a commit.
- **Test command:** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings <path>`. DB-gated tests need `TEST_DATABASE_URL` set (docker `flashback-postgres`, db `flashback_test`, role `flashback`, port 15432). The `tests/conftest.py` `schema_applied` fixture applies ALL `migrations/*.up.sql` in sorted order, so migration 0033 is picked up automatically.
- **Baselines (must not regress):** no-DB suite ~14 pre-existing failures; DB-gated suite ~24-28 pre-existing failures. SP5 is additive — no NEW failures, and the existing contradiction-logging test is *updated* (Task 7), not broken.
- **Invariant #5 (supersession repoints all edges in one tx)** — SP5 extends this to its own records (Task 8).
- **Invariant #19 (retrieval intent-gated)** — same-event links surface ONLY on `recall` (Task 9/10).
- **Code over LLM (§10):** the LLM only emits the `same_event`/`contradiction` verdict; all persistence, canonicalization, repointing, and provenance resolution are code-side.
- **Contradictions never reach the agent** — no prompt sees contradiction data. Same-event links DO (links only).
- Migration number is **0033** (latest existing is 0032).

---

## File Structure

**New files:**
- `migrations/0033_same_event_and_contradictions.up.sql` / `.down.sql` — the two record tables + indexes.
- `src/flashback/moment_links/__init__.py` — package exports.
- `src/flashback/moment_links/schema.py` — `SameEventLink`, `ContradictionItem` pydantic models.
- `src/flashback/moment_links/repository.py` — sync inserts + repoint (worker tx), async reads/actions (HTTP).
- `src/flashback/http/routes/moment_links.py` — `event_links_router` + `contradictions_router`.
- Test files under `tests/moment_links/`, `tests/workers/extraction/`, `tests/retrieval/`, `tests/response_generator/`, `tests/http/`.

**Modified files:**
- `src/flashback/workers/extraction/schema.py` — extend `CompatibilityVerdict`.
- `src/flashback/workers/extraction/prompts.py` — verdict enum + guidance for `same_event`.
- `src/flashback/workers/extraction/persistence.py` — `MomentDecision.same_event_ids`; write records in `persist_extraction`; repoint in `_supersede_moment`; new `person_id` thread to the record writes.
- `src/flashback/workers/extraction/worker.py` — route `same_event` in the candidate loop.
- `src/flashback/retrieval/queries.py` + `service.py` — `get_same_event_linked_moments`.
- `src/flashback/orchestrator/state.py` + `steps/retrieve.py` + `steps/generate_response.py` — `linked_account_moments`.
- `src/flashback/response_generator/schema.py` + `context.py` + `prompts.py` — render `<linked_accounts>`.
- `src/flashback/http/app.py` — register the two routers.
- `CLAUDE.md`, `API.md`, `NODE_INTEGRATION.md` — invariant #28 + endpoint docs.

---

## Task 1: Migration 0033 — record tables

**Files:**
- Create: `migrations/0033_same_event_and_contradictions.up.sql`
- Create: `migrations/0033_same_event_and_contradictions.down.sql`
- Test: `tests/moment_links/test_migration.py`

**Interfaces:**
- Produces: tables `moment_same_event_links` (`id, person_id, moment_a_id, moment_b_id, reason, status['active'|'unlinked'], acknowledged_at, created_at, updated_at`) and `moment_contradictions` (`id, person_id, moment_a_id, moment_b_id, reason, status['pending'|'dismissed'], created_at, resolved_at`). Both: `CHECK (moment_a_id <> moment_b_id)`, partial unique index on `(moment_a_id, moment_b_id)` over the live status.

- [ ] **Step 1: Write the up migration**

Create `migrations/0033_same_event_and_contradictions.up.sql`:

```sql
-- SP5: same-event linking + contradiction review records.
-- Both tables store moment ids only; provenance is resolved live via JOIN
-- to moments at read time (spec D5). A/B order is canonicalized on insert
-- (smaller UUID first) so the partial unique index collapses mirror pairs.

CREATE TABLE moment_same_event_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES persons(id),
    moment_a_id     UUID NOT NULL REFERENCES moments(id),
    moment_b_id     UUID NOT NULL REFERENCES moments(id),
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    acknowledged_at TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT moment_same_event_links_distinct CHECK (moment_a_id <> moment_b_id)
);

CREATE INDEX moment_same_event_links_person_status_idx
    ON moment_same_event_links (person_id, status);
CREATE INDEX moment_same_event_links_a_idx ON moment_same_event_links (moment_a_id);
CREATE INDEX moment_same_event_links_b_idx ON moment_same_event_links (moment_b_id);
CREATE UNIQUE INDEX moment_same_event_links_pair_active_uniq
    ON moment_same_event_links (moment_a_id, moment_b_id)
    WHERE status = 'active';

CREATE TABLE moment_contradictions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES persons(id),
    moment_a_id     UUID NOT NULL REFERENCES moments(id),
    moment_b_id     UUID NOT NULL REFERENCES moments(id),
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ NULL,
    CONSTRAINT moment_contradictions_distinct CHECK (moment_a_id <> moment_b_id)
);

CREATE INDEX moment_contradictions_person_status_idx
    ON moment_contradictions (person_id, status);
CREATE INDEX moment_contradictions_a_idx ON moment_contradictions (moment_a_id);
CREATE INDEX moment_contradictions_b_idx ON moment_contradictions (moment_b_id);
CREATE UNIQUE INDEX moment_contradictions_pair_pending_uniq
    ON moment_contradictions (moment_a_id, moment_b_id)
    WHERE status = 'pending';
```

- [ ] **Step 2: Write the down migration**

Create `migrations/0033_same_event_and_contradictions.down.sql`:

```sql
DROP TABLE IF EXISTS moment_contradictions;
DROP TABLE IF EXISTS moment_same_event_links;
```

- [ ] **Step 3: Write the failing test**

Create `tests/moment_links/test_migration.py` (and `tests/moment_links/__init__.py` empty):

```python
import os
import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_tables_exist(async_db_pool):
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT to_regclass('moment_same_event_links'), "
                "to_regclass('moment_contradictions')"
            )
            a, b = await cur.fetchone()
    assert a is not None
    assert b is not None


@db_only
async def test_distinct_check_rejects_self_pair(async_db_pool):
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM persons LIMIT 1")
            row = await cur.fetchone()
            if row is None:
                pytest.skip("no person rows to reference")
            # A self-pair must violate the CHECK constraint.
            with pytest.raises(Exception):
                await cur.execute(
                    "INSERT INTO moment_same_event_links "
                    "(person_id, moment_a_id, moment_b_id) VALUES (%s, %s, %s)",
                    (row[0], row[0], row[0]),
                )
            await conn.rollback()
```

- [ ] **Step 4: Run the test (expect PASS once migrations apply)**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/moment_links/test_migration.py`
Expected: PASS (the `schema_applied` fixture applies 0033). If `TEST_DATABASE_URL` is unset, tests skip — that is acceptable; note it in the checkpoint.

- [ ] **Step 5: Checkpoint** — confirm both tables exist and the down migration is syntactically valid (`DROP TABLE` order: contradictions first is harmless since no FK between them). No commit.

---

## Task 2: `moment_links` schema + sync insert helpers

**Files:**
- Create: `src/flashback/moment_links/__init__.py`
- Create: `src/flashback/moment_links/schema.py`
- Create: `src/flashback/moment_links/repository.py`
- Test: `tests/moment_links/test_repository_insert.py`

**Interfaces:**
- Produces:
  - `schema.SameEventLink` (pydantic): `id: UUID, person_id: UUID, moment_a_id: UUID, moment_b_id: UUID, reason: str | None, status: str, acknowledged_at: datetime | None, created_at: datetime, told_by_a_user_id: UUID | None, told_by_a_display_name: str | None, told_by_b_user_id: UUID | None, told_by_b_display_name: str | None, moment_a_title: str, moment_b_title: str` (the `told_by_*` / title fields are populated only by the read path's JOIN; default `None`/`""`).
  - `schema.ContradictionItem` (pydantic): same id/person/pair/reason/status/created_at + `resolved_at: datetime | None` + the same JOIN-populated `told_by_*` / title fields.
  - `repository.canonical_pair(a: str, b: str) -> tuple[str, str]` — returns `(min, max)` by string compare so A/B order is stable.
  - `repository.insert_same_event_link(cursor, *, person_id: str, moment_a_id: str, moment_b_id: str, reason: str | None) -> str | None` — sync cursor; canonicalizes order; idempotent via `ON CONFLICT DO NOTHING` against the active partial unique index; returns the row id, or `None` if a conflicting active row already exists.
  - `repository.insert_contradiction(cursor, *, person_id: str, moment_a_id: str, moment_b_id: str, reason: str | None) -> str | None` — same shape against the pending partial unique index.

- [ ] **Step 1: Write `schema.py`**

```python
"""Pydantic models for SP5 same-event links + contradiction review items."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SameEventLink(BaseModel):
    id: UUID
    person_id: UUID
    moment_a_id: UUID
    moment_b_id: UUID
    reason: str | None = None
    status: str
    acknowledged_at: datetime | None = None
    created_at: datetime
    # Live-resolved via JOIN to moments at read time (spec D5).
    moment_a_title: str = ""
    moment_b_title: str = ""
    told_by_a_user_id: UUID | None = None
    told_by_a_display_name: str | None = None
    told_by_b_user_id: UUID | None = None
    told_by_b_display_name: str | None = None


class ContradictionItem(BaseModel):
    id: UUID
    person_id: UUID
    moment_a_id: UUID
    moment_b_id: UUID
    reason: str | None = None
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    moment_a_title: str = ""
    moment_b_title: str = ""
    told_by_a_user_id: UUID | None = None
    told_by_a_display_name: str | None = None
    told_by_b_user_id: UUID | None = None
    told_by_b_display_name: str | None = None
```

- [ ] **Step 2: Write the canonicalization + sync inserts in `repository.py`**

```python
"""Persistence for SP5 same-event links + contradiction review items.

Sync helpers run inside the Extraction Worker's transaction (psycopg sync
cursor). Async helpers serve the read/action HTTP endpoints. Record rows
store moment ids only; told_by_* is resolved live via JOIN to moments at
read time (spec D5).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("flashback.moment_links")


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """Stable A/B order (smaller UUID string first) so mirror pairs collapse
    under the partial unique index."""
    return (a, b) if str(a) <= str(b) else (b, a)


def insert_same_event_link(
    cursor, *, person_id: str, moment_a_id: str, moment_b_id: str, reason: str | None
) -> str | None:
    a, b = canonical_pair(moment_a_id, moment_b_id)
    cursor.execute(
        """
        INSERT INTO moment_same_event_links
              (person_id, moment_a_id, moment_b_id, reason)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (moment_a_id, moment_b_id) WHERE status = 'active'
        DO NOTHING
        RETURNING id::text
        """,
        (person_id, a, b, reason),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def insert_contradiction(
    cursor, *, person_id: str, moment_a_id: str, moment_b_id: str, reason: str | None
) -> str | None:
    a, b = canonical_pair(moment_a_id, moment_b_id)
    cursor.execute(
        """
        INSERT INTO moment_contradictions
              (person_id, moment_a_id, moment_b_id, reason)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (moment_a_id, moment_b_id) WHERE status = 'pending'
        DO NOTHING
        RETURNING id::text
        """,
        (person_id, a, b, reason),
    )
    row = cursor.fetchone()
    return row[0] if row else None
```

> Note: psycopg accepts the partial-index `ON CONFLICT ... WHERE` inference form. If the running Postgres rejects the inline `WHERE` on the conflict target, fall back to naming the index is NOT allowed in `ON CONFLICT`; instead use `ON CONFLICT (moment_a_id, moment_b_id) DO NOTHING` is also invalid for partial indexes — keep the `WHERE status='active'` predicate exactly as written above, which matches the partial index from Task 1 and is the supported inference form.

- [ ] **Step 3: Write `__init__.py`**

```python
"""SP5: same-event links + contradiction review."""

from .repository import (
    canonical_pair,
    insert_contradiction,
    insert_same_event_link,
)
from .schema import ContradictionItem, SameEventLink

__all__ = [
    "canonical_pair",
    "insert_contradiction",
    "insert_same_event_link",
    "ContradictionItem",
    "SameEventLink",
]
```

- [ ] **Step 4: Write the failing test**

Create `tests/moment_links/test_repository_insert.py`:

```python
import os
import pytest
from uuid import uuid4
from psycopg.types.json import Json

from flashback.moment_links import (
    canonical_pair,
    insert_same_event_link,
    insert_contradiction,
)

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def test_canonical_pair_orders_by_string():
    a, b = "ffff", "0000"
    assert canonical_pair(a, b) == ("0000", "ffff")
    assert canonical_pair(b, a) == ("0000", "ffff")


async def _person_and_two_moments(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text"
            )
            (pid,) = await cur.fetchone()
            ids = []
            for t in ("A", "B"):
                await cur.execute(
                    "INSERT INTO moments (person_id, title, narrative, status) "
                    "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
                    (pid, t),
                )
                ids.append((await cur.fetchone())[0])
            await conn.commit()
    return pid, ids[0], ids[1]


@db_only
async def test_insert_same_event_link_is_idempotent(async_db_pool):
    pid, m1, m2 = await _person_and_two_moments(async_db_pool)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            first = insert_same_event_link(
                cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason="same day"
            )
            # Mirror order, second time -> conflict, no new row.
            second = insert_same_event_link(
                cur, person_id=pid, moment_a_id=m2, moment_b_id=m1, reason="x"
            )
            await conn.commit()
    assert first is not None
    assert second is None


@db_only
async def test_insert_contradiction_writes_pending(async_db_pool):
    pid, m1, m2 = await _person_and_two_moments(async_db_pool)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            cid = insert_contradiction(
                cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason="age clash"
            )
            await cur.execute(
                "SELECT status FROM moment_contradictions WHERE id = %s", (cid,)
            )
            (status,) = await cur.fetchone()
            await conn.commit()
    assert status == "pending"
```

> The sync `insert_*` helpers take a sync cursor; in these async tests they are called on the async cursor object, which exposes `.execute`/`.fetchone` as coroutines — so wrap calls accordingly. If the helper is sync-only, adapt the test to use a sync psycopg connection instead (`psycopg.connect(_DB)`), mirroring `tests/workers/extraction` DB tests. Prefer the sync-connection form to match real worker usage.

- [ ] **Step 5: Adjust test to sync connection (match worker usage)**

Rewrite the DB-gated tests to open a sync connection so the sync helpers are exercised exactly as the worker uses them:

```python
import psycopg

def _sync_person_and_moments():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    ids = []
    for t in ("A", "B"):
        cur.execute(
            "INSERT INTO moments (person_id, title, narrative, status) "
            "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
            (pid, t),
        )
        ids.append(cur.fetchone()[0])
    return conn, cur, pid, ids[0], ids[1]


@db_only
def test_insert_same_event_link_is_idempotent_sync():
    conn, cur, pid, m1, m2 = _sync_person_and_moments()
    try:
        first = insert_same_event_link(cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason="same day")
        second = insert_same_event_link(cur, person_id=pid, moment_a_id=m2, moment_b_id=m1, reason="x")
    finally:
        conn.close()
    assert first is not None
    assert second is None
```

Replace the two `@db_only async def` DB tests with sync equivalents; keep `test_canonical_pair_orders_by_string` as a pure unit test.

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/moment_links/test_repository_insert.py`
Expected: `test_canonical_pair_orders_by_string` PASS always; DB tests PASS with DB up, SKIP otherwise.

- [ ] **Step 7: Checkpoint** — no commit.

---

## Task 3: `repoint_records_on_supersession` (sync)

**Files:**
- Modify: `src/flashback/moment_links/repository.py`
- Modify: `src/flashback/moment_links/__init__.py` (export)
- Test: `tests/moment_links/test_repoint.py`

**Interfaces:**
- Consumes: `canonical_pair` (Task 2).
- Produces: `repository.repoint_records_on_supersession(cursor, *, old_id: str, new_id: str) -> None` — sync cursor. For active `moment_same_event_links` and pending `moment_contradictions` rows referencing `old_id` (as A or B): if the partner side is `new_id` (would become a self-pair) → terminate the row (`status='unlinked'` for links, `status='dismissed'`+`resolved_at=now()` for contradictions); otherwise substitute `old_id`→`new_id` and re-canonicalize A/B order. Terminal rows are skipped.

- [ ] **Step 1: Write the failing test**

Create `tests/moment_links/test_repoint.py`:

```python
import os
import psycopg
import pytest

from flashback.moment_links import insert_same_event_link
from flashback.moment_links.repository import repoint_records_on_supersession

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _setup():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    ids = []
    for t in ("A", "B", "C"):
        cur.execute(
            "INSERT INTO moments (person_id, title, narrative, status) "
            "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
            (pid, t),
        )
        ids.append(cur.fetchone()[0])
    return conn, cur, pid, ids  # A, B, C


@db_only
def test_repoint_substitutes_old_for_new():
    conn, cur, pid, (mA, mB, mC) = _setup()
    try:
        lid = insert_same_event_link(cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="r")
        # B is superseded by C: link should now reference A & C.
        repoint_records_on_supersession(cur, old_id=mB, new_id=mC)
        cur.execute(
            "SELECT moment_a_id::text, moment_b_id::text, status "
            "FROM moment_same_event_links WHERE id = %s", (lid,)
        )
        a, b, status = cur.fetchone()
    finally:
        conn.close()
    assert status == "active"
    assert {a, b} == {mA, mC}


@db_only
def test_repoint_collapses_self_pair():
    conn, cur, pid, (mA, mB, mC) = _setup()
    try:
        lid = insert_same_event_link(cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="r")
        # A is superseded by B -> link would become B&B -> collapse to unlinked.
        repoint_records_on_supersession(cur, old_id=mA, new_id=mB)
        cur.execute("SELECT status FROM moment_same_event_links WHERE id = %s", (lid,))
        (status,) = cur.fetchone()
    finally:
        conn.close()
    assert status == "unlinked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/moment_links/test_repoint.py`
Expected: FAIL with `ImportError: cannot import name 'repoint_records_on_supersession'`.

- [ ] **Step 3: Implement `repoint_records_on_supersession`**

Append to `repository.py`:

```python
def repoint_records_on_supersession(cursor, *, old_id: str, new_id: str) -> None:
    """Keep SP5 records pointing at the active moment after a supersession.

    Extends invariant #5 to same-event links + contradiction review items.
    Active links / pending contradictions referencing ``old_id`` are either
    collapsed (if repointing would create a self-pair) or have ``old_id``
    swapped for ``new_id`` with the A/B order re-canonicalized. Terminal rows
    (unlinked / dismissed) are left untouched.
    """
    _repoint_table(
        cursor,
        table="moment_same_event_links",
        live_status="active",
        terminal_status="unlinked",
        terminal_extra="",
        old_id=old_id,
        new_id=new_id,
    )
    _repoint_table(
        cursor,
        table="moment_contradictions",
        live_status="pending",
        terminal_status="dismissed",
        terminal_extra=", resolved_at = now()",
        old_id=old_id,
        new_id=new_id,
    )


def _repoint_table(
    cursor, *, table, live_status, terminal_status, terminal_extra, old_id, new_id
):
    cursor.execute(
        f"""
        SELECT id::text, moment_a_id::text, moment_b_id::text
          FROM {table}
         WHERE status = %s
           AND (moment_a_id = %s OR moment_b_id = %s)
        """,
        (live_status, old_id, old_id),
    )
    for row_id, a, b in cursor.fetchall():
        partner = b if a == old_id else a
        if partner == new_id:
            cursor.execute(
                f"UPDATE {table} SET status = %s{terminal_extra} WHERE id = %s",
                (terminal_status, row_id),
            )
            continue
        na, nb = canonical_pair(new_id, partner)
        cursor.execute(
            f"UPDATE {table} SET moment_a_id = %s, moment_b_id = %s WHERE id = %s",
            (na, nb, row_id),
        )
```

Add `repoint_records_on_supersession` to `__init__.py` `__all__` and imports.

> Edge case: if repointing would collide with another *existing* active/pending row for the same canonical pair (rare), the `UPDATE` violates the partial unique index and raises. This is acceptable inside the worker tx — it would roll back the whole segment, which is safer than silently dropping a record. The compatibility search makes such a pre-existing collision extremely unlikely (the new moment is brand-new this segment). Do NOT add `ON CONFLICT` handling to the UPDATE; leave it to surface.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/moment_links/test_repoint.py`
Expected: PASS (DB up) / SKIP (no DB).

- [ ] **Step 5: Checkpoint** — no commit.

---

## Task 4: Async read + action helpers

**Files:**
- Modify: `src/flashback/moment_links/repository.py`
- Modify: `src/flashback/moment_links/__init__.py`
- Test: `tests/moment_links/test_repository_reads.py`

**Interfaces:**
- Produces (all async, take an async cursor):
  - `list_event_links_async(cursor, *, person_id: str, include_acknowledged: bool = False) -> list[SameEventLink]`
  - `acknowledge_event_link_async(cursor, *, link_id: str) -> bool`
  - `unlink_event_link_async(cursor, *, link_id: str) -> bool`
  - `list_contradictions_async(cursor, *, person_id: str) -> list[ContradictionItem]`
  - `dismiss_contradiction_async(cursor, *, item_id: str) -> bool`
- All list queries JOIN `moments` (aliased `ma`, `mb`) for titles and JOIN `collaborator_onboarding` twice for live `told_by_*_display_name` (spec D5), exactly like `GET_ENTITIES_BY_IDS_SQL`.

- [ ] **Step 1: Write the failing test**

Create `tests/moment_links/test_repository_reads.py`:

```python
import os
import pytest
from uuid import uuid4

from flashback.moment_links import (
    insert_same_event_link,
    insert_contradiction,
)
from flashback.moment_links.repository import (
    list_event_links_async,
    acknowledge_event_link_async,
    unlink_event_link_async,
    list_contradictions_async,
    dismiss_contradiction_async,
)

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


async def _seed(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
            (pid,) = await cur.fetchone()
            ravi = str(uuid4())
            await cur.execute(
                "INSERT INTO collaborator_onboarding "
                "(person_id, user_id, voice_anchor_text, display_name, status) "
                "VALUES (%s, %s, 'his son', 'Ravi', 'active')",
                (pid, ravi),
            )
            mids = []
            for t, tb in (("A", None), ("B", ravi)):
                await cur.execute(
                    "INSERT INTO moments (person_id, title, narrative, status, told_by_user_id, told_by_display_name) "
                    "VALUES (%s, %s, 'n', 'active', %s, %s) RETURNING id::text",
                    (pid, t, tb, "Ravi" if tb else None),
                )
                mids.append((await cur.fetchone())[0])
            await conn.commit()
    return pid, mids[0], mids[1]


@db_only
async def test_list_event_links_resolves_live_provenance(async_db_pool):
    pid, mA, mB = await _seed(async_db_pool)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            insert_same_event_link(cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="same day")
            await conn.commit()
        async with conn.cursor() as cur:
            links = await list_event_links_async(cur, person_id=pid)
    assert len(links) == 1
    titles = {links[0].moment_a_title, links[0].moment_b_title}
    assert titles == {"A", "B"}
    # The Ravi side resolves a display name; the creator-era side stays None.
    names = {links[0].told_by_a_display_name, links[0].told_by_b_display_name}
    assert "Ravi" in names


@db_only
async def test_acknowledge_and_unlink(async_db_pool):
    pid, mA, mB = await _seed(async_db_pool)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            lid = insert_same_event_link(cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="r")
            await conn.commit()
        async with conn.cursor() as cur:
            assert await acknowledge_event_link_async(cur, link_id=lid) is True
            assert await unlink_event_link_async(cur, link_id=lid) is True
            await conn.commit()
        async with conn.cursor() as cur:
            # Unlinked links are excluded from the default feed.
            assert await list_event_links_async(cur, person_id=pid, include_acknowledged=True) == []


@db_only
async def test_list_and_dismiss_contradiction(async_db_pool):
    pid, mA, mB = await _seed(async_db_pool)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            cid = insert_contradiction(cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="clash")
            await conn.commit()
        async with conn.cursor() as cur:
            items = await list_contradictions_async(cur, person_id=pid)
            assert len(items) == 1
            assert await dismiss_contradiction_async(cur, item_id=cid) is True
            await conn.commit()
        async with conn.cursor() as cur:
            assert await list_contradictions_async(cur, person_id=pid) == []
```

> Note `insert_same_event_link` is sync; called on an async cursor here it returns a coroutine-free value only if the async cursor's `execute` is awaited. To keep the worker's sync helper truly sync AND reuse it in async tests, the test seeds links via a sync connection instead. Adjust: open `psycopg.connect(_DB)` for the insert, then use the async pool for the read assertions. Apply the same sync-insert / async-read split used in Task 3's reasoning.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/moment_links/test_repository_reads.py`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement the async helpers**

Append to `repository.py` (the two list SQLs mirror `GET_ENTITIES_BY_IDS_SQL`'s double `collaborator_onboarding` JOIN, once per side):

```python
_EVENT_LINKS_SQL = """
SELECT l.id, l.person_id, l.moment_a_id, l.moment_b_id, l.reason, l.status,
       l.acknowledged_at, l.created_at,
       ma.title AS moment_a_title, mb.title AS moment_b_title,
       ma.told_by_user_id AS told_by_a_user_id, coa.display_name AS told_by_a_display_name,
       mb.told_by_user_id AS told_by_b_user_id, cob.display_name AS told_by_b_display_name
  FROM moment_same_event_links l
  JOIN active_moments ma ON ma.id = l.moment_a_id
  JOIN active_moments mb ON mb.id = l.moment_b_id
  LEFT JOIN collaborator_onboarding coa
        ON coa.person_id = l.person_id AND coa.user_id = ma.told_by_user_id AND coa.status = 'active'
  LEFT JOIN collaborator_onboarding cob
        ON cob.person_id = l.person_id AND cob.user_id = mb.told_by_user_id AND cob.status = 'active'
 WHERE l.person_id = %(person_id)s
   AND l.status = 'active'
   AND (%(include_ack)s OR l.acknowledged_at IS NULL)
 ORDER BY l.created_at DESC
"""

_CONTRADICTIONS_SQL = """
SELECT c.id, c.person_id, c.moment_a_id, c.moment_b_id, c.reason, c.status,
       c.created_at, c.resolved_at,
       ma.title AS moment_a_title, mb.title AS moment_b_title,
       ma.told_by_user_id AS told_by_a_user_id, coa.display_name AS told_by_a_display_name,
       mb.told_by_user_id AS told_by_b_user_id, cob.display_name AS told_by_b_display_name
  FROM moment_contradictions c
  JOIN active_moments ma ON ma.id = c.moment_a_id
  JOIN active_moments mb ON mb.id = c.moment_b_id
  LEFT JOIN collaborator_onboarding coa
        ON coa.person_id = c.person_id AND coa.user_id = ma.told_by_user_id AND coa.status = 'active'
  LEFT JOIN collaborator_onboarding cob
        ON cob.person_id = c.person_id AND cob.user_id = mb.told_by_user_id AND cob.status = 'active'
 WHERE c.person_id = %(person_id)s
   AND c.status = 'pending'
 ORDER BY c.created_at DESC
"""


async def list_event_links_async(cursor, *, person_id, include_acknowledged=False):
    from .schema import SameEventLink
    await cursor.execute(
        _EVENT_LINKS_SQL, {"person_id": person_id, "include_ack": include_acknowledged}
    )
    rows = await cursor.fetchall()
    return [_row_to_link(SameEventLink, r) for r in rows]


async def list_contradictions_async(cursor, *, person_id):
    from .schema import ContradictionItem
    await cursor.execute(_CONTRADICTIONS_SQL, {"person_id": person_id})
    rows = await cursor.fetchall()
    return [_row_to_contradiction(ContradictionItem, r) for r in rows]


def _row_to_link(model, r):
    return model(
        id=r[0], person_id=r[1], moment_a_id=r[2], moment_b_id=r[3], reason=r[4],
        status=r[5], acknowledged_at=r[6], created_at=r[7],
        moment_a_title=r[8] or "", moment_b_title=r[9] or "",
        told_by_a_user_id=r[10], told_by_a_display_name=r[11],
        told_by_b_user_id=r[12], told_by_b_display_name=r[13],
    )


def _row_to_contradiction(model, r):
    return model(
        id=r[0], person_id=r[1], moment_a_id=r[2], moment_b_id=r[3], reason=r[4],
        status=r[5], created_at=r[6], resolved_at=r[7],
        moment_a_title=r[8] or "", moment_b_title=r[9] or "",
        told_by_a_user_id=r[10], told_by_a_display_name=r[11],
        told_by_b_user_id=r[12], told_by_b_display_name=r[13],
    )


async def acknowledge_event_link_async(cursor, *, link_id):
    await cursor.execute(
        "UPDATE moment_same_event_links SET acknowledged_at = now(), updated_at = now() "
        "WHERE id = %s AND status = 'active'",
        (link_id,),
    )
    return cursor.rowcount > 0


async def unlink_event_link_async(cursor, *, link_id):
    await cursor.execute(
        "UPDATE moment_same_event_links SET status = 'unlinked', updated_at = now() "
        "WHERE id = %s AND status = 'active'",
        (link_id,),
    )
    return cursor.rowcount > 0


async def dismiss_contradiction_async(cursor, *, item_id):
    await cursor.execute(
        "UPDATE moment_contradictions SET status = 'dismissed', resolved_at = now() "
        "WHERE id = %s AND status = 'pending'",
        (item_id,),
    )
    return cursor.rowcount > 0
```

Export all five async functions from `__init__.py`.

- [ ] **Step 4: Apply the sync-insert / async-read split in the test** (per the Step 1 note), then run:

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/moment_links/test_repository_reads.py`
Expected: PASS (DB up) / SKIP (no DB).

- [ ] **Step 5: Checkpoint** — no commit.

---

## Task 5: `same_event` compatibility verdict

**Files:**
- Modify: `src/flashback/workers/extraction/schema.py:28`
- Modify: `src/flashback/workers/extraction/prompts.py:403-443`
- Test: `tests/workers/extraction/test_compatibility_llm.py`

**Interfaces:**
- Produces: `CompatibilityVerdict = Literal["refinement", "same_event", "contradiction", "independent"]`; the `COMPATIBILITY_TOOL` enum and `COMPATIBILITY_SYSTEM_PROMPT` include `same_event`.

- [ ] **Step 1: Write the failing test**

Add to `tests/workers/extraction/test_compatibility_llm.py`:

```python
def test_compatibility_verdict_includes_same_event():
    from flashback.workers.extraction.schema import CompatibilityVerdict
    from typing import get_args
    assert "same_event" in get_args(CompatibilityVerdict)


def test_compatibility_tool_enum_includes_same_event():
    from flashback.workers.extraction.prompts import COMPATIBILITY_TOOL
    enum = COMPATIBILITY_TOOL.input_schema["properties"]["verdict"]["enum"]
    assert "same_event" in enum
    assert set(enum) == {"refinement", "same_event", "contradiction", "independent"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_compatibility_llm.py -k same_event`
Expected: FAIL (`same_event` not in enum).

- [ ] **Step 3: Update the verdict type**

In `schema.py:28`:

```python
CompatibilityVerdict = Literal["refinement", "same_event", "contradiction", "independent"]
```

- [ ] **Step 4: Update the tool enum + prompt**

In `prompts.py`, the `COMPATIBILITY_TOOL` enum (~line 415):

```python
                "enum": ["refinement", "same_event", "contradiction", "independent"],
```

In `COMPATIBILITY_SYSTEM_PROMPT`, insert the `same_event` clause between `refinement` and `contradiction` (after line 432):

```python
- `same_event`: They describe the SAME real-world event or occasion from \
different angles, and both accounts are valid and complementary (e.g. two \
people recalling the same wedding). Neither supersedes the other; the system \
links them so the other account can be surfaced. Prefer `independent` if you \
are not confident they are one shared occasion.
```

Update the closing guidance line (currently "When in doubt, prefer `independent`...") to keep `independent` as the safe default and add: "Use `same_event` only for one clearly-shared occasion; use `contradiction` when accounts of one event conflict on a fact (the conflict outranks the link)."

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_compatibility_llm.py`
Expected: PASS.

- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 6: Worker routes `same_event` (record all)

**Files:**
- Modify: `src/flashback/workers/extraction/persistence.py:49-66` (`MomentDecision`)
- Modify: `src/flashback/workers/extraction/worker.py:669-683` (candidate loop)
- Test: `tests/workers/extraction/test_decision_routing.py` (new)

**Interfaces:**
- Consumes: `CompatibilityVerdict` (Task 5).
- Produces: `MomentDecision.same_event_ids: list[str]` (default empty). Loop appends each `same_event` candidate id and each `contradiction` candidate id (records ALL, continues scanning); `refinement` still sets `supersedes_id` and breaks.

- [ ] **Step 1: Write the failing test**

Create `tests/workers/extraction/test_decision_routing.py`:

```python
from dataclasses import dataclass
from flashback.workers.extraction.persistence import MomentDecision


def test_moment_decision_has_same_event_ids():
    d = MomentDecision(moment=object())
    assert d.same_event_ids == []
    assert d.contradicts_ids == []
    assert d.supersedes_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_decision_routing.py`
Expected: FAIL (`AttributeError: 'MomentDecision' object has no attribute 'same_event_ids'`).

- [ ] **Step 3: Add the field**

In `persistence.py` `MomentDecision` (after `contradicts_ids`):

```python
    same_event_ids: list[str] = field(default_factory=list)
```

Update the docstring to mention `same_event_ids` is the list of existing moments judged the same event (auto-linked).

- [ ] **Step 4: Route the verdict in the worker loop**

In `worker.py`, the candidate loop (after the `contradiction` branch, ~line 682):

```python
                if response.verdict == "same_event":
                    decision.same_event_ids.append(candidate.id)
                # independent — keep looking
```

(Leave `refinement` break and `contradiction` append unchanged.)

- [ ] **Step 5: Run the test**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_decision_routing.py`
Expected: PASS.

- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 7: Persistence writes the records

**Files:**
- Modify: `src/flashback/workers/extraction/persistence.py:236-260` (per-moment loop) + imports
- Test: `tests/workers/extraction/test_persistence_records.py` (new)
- Update: any existing test asserting `extraction.contradiction_logged` (search `tests/` for it)

**Interfaces:**
- Consumes: `insert_same_event_link`, `insert_contradiction` (Task 2); `MomentDecision.same_event_ids`/`contradicts_ids`.
- Produces: inside `persist_extraction`'s per-moment loop, for each `same_event_ids` entry → `insert_same_event_link(cursor, person_id=person.id, moment_a_id=moment_id, moment_b_id=cid, reason=None)`; for each `contradicts_ids` entry → `insert_contradiction(...)`. The structlog-only block is removed.

- [ ] **Step 1: Write the failing test**

Create `tests/workers/extraction/test_persistence_records.py` (DB-gated; mirrors existing extraction persistence DB tests — reuse their helper for building `PersonRow` + `ExtractionResult` + `MomentDecision`; if a shared helper exists in `tests/workers/extraction/conftest.py`, use it). Skeleton:

```python
import os
import psycopg
import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
def test_same_event_decision_writes_link(persisted_person_and_existing_moment):
    """A MomentDecision.same_event_ids entry writes one active link row."""
    # Arrange: an existing active moment `cid` for person `pid`; a new
    # ExtractionResult with one moment whose decision.same_event_ids=[cid].
    # Act: run persist_extraction within a sync tx.
    # Assert: exactly one moment_same_event_links row (status active) linking
    # the new moment id and cid.
    ...


@db_only
def test_contradiction_decision_writes_pending_row(persisted_person_and_existing_moment):
    """A MomentDecision.contradicts_ids entry writes one pending contradiction."""
    ...
```

Fill the `...` using the existing extraction-persistence DB test fixtures/patterns in `tests/workers/extraction/` (build `PersonRow`, an `ExtractionResult` with one `ExtractedMoment`, a `MomentDecision` with `same_event_ids=[cid]` / `contradicts_ids=[cid]`, call `persist_extraction(cur, person=..., extraction=..., moment_decisions=[...], told_by_user_id=..., told_by_display_name=...)`, then SELECT the record tables). If no reusable fixture exists, insert the person + one existing moment by raw SQL as in Task 3's `_setup`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_persistence_records.py`
Expected: FAIL (no rows written — current code only logs).

- [ ] **Step 3: Wire the inserts into persistence**

At the top of `persistence.py`, add:

```python
from flashback.moment_links import insert_contradiction, insert_same_event_link
```

Replace the `for cid in decision.contradicts_ids:` log block (lines 255-260) with:

```python
        for cid in decision.same_event_ids:
            insert_same_event_link(
                cursor,
                person_id=person.id,
                moment_a_id=moment_id,
                moment_b_id=cid,
                reason=None,
            )
        for cid in decision.contradicts_ids:
            insert_contradiction(
                cursor,
                person_id=person.id,
                moment_a_id=moment_id,
                moment_b_id=cid,
                reason=None,
            )
```

> `reason=None` for now — the compatibility `reasoning` is not threaded into `MomentDecision` today. Threading it is a nice-to-have, out of scope (spec keeps `reason` nullable). Do NOT expand scope to capture it.

- [ ] **Step 4: Update the old contradiction-logging test**

Search: `grep -rn "contradiction_logged" tests/`. If a test asserts the structlog line, update it to assert a `moment_contradictions` row is written instead (or delete the assertion if it was a pure log check). Run that test file to confirm green.

- [ ] **Step 5: Run the new + updated tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_persistence_records.py`
Expected: PASS (DB up) / SKIP (no DB).

- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 8: Supersession repoints SP5 records

**Files:**
- Modify: `src/flashback/workers/extraction/persistence.py:1051-1063` (`_supersede_moment`)
- Test: `tests/workers/extraction/test_persistence_records.py` (extend)

**Interfaces:**
- Consumes: `repoint_records_on_supersession` (Task 3).
- Produces: `_supersede_moment` calls `repoint_records_on_supersession(cursor, old_id=old_moment_id, new_id=new_moment_id)` after the edge repointing.

- [ ] **Step 1: Write the failing test**

Add to `tests/workers/extraction/test_persistence_records.py`:

```python
@db_only
def test_supersession_repoints_active_link():
    """When a linked moment is superseded, its active link follows to the new id."""
    # Arrange: moments A, B, C for one person; an active link A<->B.
    # Act: call _supersede_moment(cur, old_moment_id=B, new_moment_id=C).
    # Assert: the link now references A & C, still status active.
    ...
```

(Build via raw SQL like Task 3's `_setup`; import `_supersede_moment` from `flashback.workers.extraction.persistence`.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_persistence_records.py -k repoints`
Expected: FAIL (link still references B).

- [ ] **Step 3: Add the repoint call**

In `persistence.py`, add the import (top, alongside Task 7's import):

```python
from flashback.moment_links import (
    insert_contradiction,
    insert_same_event_link,
    repoint_records_on_supersession,
)
```

At the end of `_supersede_moment` (after the outbound-edge DELETE, line 1105):

```python
    repoint_records_on_supersession(
        cursor, old_id=old_moment_id, new_id=new_moment_id
    )
```

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/workers/extraction/test_persistence_records.py`
Expected: PASS (DB up) / SKIP (no DB).

- [ ] **Step 5: Checkpoint** — no commit.

---

## Task 9: Retrieval — fetch same-event-linked moments

**Files:**
- Modify: `src/flashback/retrieval/queries.py` (add `GET_SAME_EVENT_LINKED_MOMENTS_SQL`)
- Modify: `src/flashback/retrieval/service.py` (add `get_same_event_linked_moments`)
- Test: `tests/retrieval/test_same_event_links.py` (new)

**Interfaces:**
- Produces: `RetrievalService.get_same_event_linked_moments(self, person_id: UUID, moment_ids: list[UUID]) -> list[MomentResult]` — for the given moment ids, returns the **active** partner moments via active `moment_same_event_links`, de-duplicated and excluding the input ids. `told_by_*` populated via the same `collaborator_onboarding` JOIN as `SEARCH_MOMENTS_SQL`. `similarity_score` is `NULL`.

- [ ] **Step 1: Write the failing test**

Create `tests/retrieval/test_same_event_links.py`:

```python
import os
import pytest
from uuid import uuid4
from tests.retrieval.conftest import insert_person

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_returns_active_partner_moments(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            ids = []
            for t in ("A", "B"):
                await cur.execute(
                    "INSERT INTO moments (person_id, title, narrative, status) "
                    "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
                    (person, t),
                )
                ids.append((await cur.fetchone())[0])
            mA, mB = ids
            await cur.execute(
                "INSERT INTO moment_same_event_links (person_id, moment_a_id, moment_b_id) "
                "VALUES (%s, %s, %s)", (person, mA, mB),
            )
            await conn.commit()
    # Querying with mA returns mB (the partner), not mA itself.
    out = await retrieval_service.get_same_event_linked_moments(person, [mA])
    assert [m.title for m in out] == ["B"]


@db_only
async def test_unlinked_excluded(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            ids = []
            for t in ("A", "B"):
                await cur.execute(
                    "INSERT INTO moments (person_id, title, narrative, status) "
                    "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
                    (person, t),
                )
                ids.append((await cur.fetchone())[0])
            mA, mB = ids
            await cur.execute(
                "INSERT INTO moment_same_event_links (person_id, moment_a_id, moment_b_id, status) "
                "VALUES (%s, %s, %s, 'unlinked')", (person, mA, mB),
            )
            await conn.commit()
    out = await retrieval_service.get_same_event_linked_moments(person, [mA])
    assert out == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/retrieval/test_same_event_links.py`
Expected: FAIL (`AttributeError: ... get_same_event_linked_moments`).

- [ ] **Step 3: Add the SQL**

In `queries.py`:

```python
GET_SAME_EVENT_LINKED_MOMENTS_SQL = """
WITH partner_ids AS (
    SELECT CASE WHEN moment_a_id = ANY(%(moment_ids)s) THEN moment_b_id
                ELSE moment_a_id END AS partner_id
    FROM   moment_same_event_links
    WHERE  person_id = %(person_id)s
      AND  status    = 'active'
      AND  (moment_a_id = ANY(%(moment_ids)s) OR moment_b_id = ANY(%(moment_ids)s))
)
SELECT DISTINCT
    m.id, m.person_id, m.title, m.narrative, m.time_anchor,
    m.life_period_estimate, m.sensory_details, m.emotional_tone,
    m.contributor_perspective, m.created_at,
    m.told_by_user_id, m.told_by_display_name,
    co.voice_anchor_text AS told_by_relationship,
    NULL::double precision AS similarity_score
FROM   active_moments m
JOIN   partner_ids p ON p.partner_id = m.id
LEFT JOIN collaborator_onboarding co
       ON co.person_id = m.person_id
      AND co.user_id   = m.told_by_user_id
      AND co.status    = 'active'
WHERE  m.person_id = %(person_id)s
  AND  m.id <> ALL(%(moment_ids)s)
ORDER  BY m.created_at DESC
"""
```

- [ ] **Step 4: Add the service method**

In `service.py`, import `GET_SAME_EVENT_LINKED_MOMENTS_SQL`, then add (mirroring `get_entities_by_ids`):

```python
    async def get_same_event_linked_moments(
        self, person_id: UUID, moment_ids: list[UUID]
    ) -> list[MomentResult]:
        """Active moments linked as the same event to any of ``moment_ids``.

        Feeds the response generator's <linked_accounts> block on recall.
        Excludes the input ids and unlinked links. No vector search.
        """
        if not moment_ids:
            return []
        rows = await self._fetch_all(
            GET_SAME_EVENT_LINKED_MOMENTS_SQL,
            {"person_id": person_id, "moment_ids": moment_ids},
        )
        return [MomentResult.model_validate(row) for row in rows]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/retrieval/test_same_event_links.py`
Expected: PASS (DB up) / SKIP (no DB).

- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 10: Orchestrator wiring — populate `linked_account_moments`

**Files:**
- Modify: `src/flashback/orchestrator/state.py:47-51`
- Modify: `src/flashback/orchestrator/steps/retrieve.py:38-49`
- Modify: `src/flashback/response_generator/schema.py` (TurnContext field — also needed by Task 11)
- Modify: `src/flashback/orchestrator/steps/generate_response.py:57-61`
- Test: `tests/orchestrator/test_linked_accounts_retrieval.py` (new)

**Interfaces:**
- Consumes: `RetrievalService.get_same_event_linked_moments` (Task 9).
- Produces: `TurnState.linked_account_moments: list[MomentResult]`; populated on `recall` only; passed to `TurnContext.linked_account_moments`.

- [ ] **Step 1: Add the state field**

In `state.py` after `related_moments` (line 47):

```python
    linked_account_moments: list[MomentResult] = field(default_factory=list)
```

- [ ] **Step 2: Add the TurnContext field**

In `response_generator/schema.py` `TurnContext`, after `related_moments` (line 96):

```python
    linked_account_moments: list[MomentResult] = Field(default_factory=list)
```

- [ ] **Step 3: Write the failing test**

Create `tests/orchestrator/test_linked_accounts_retrieval.py` (no DB — fake retrieval service):

```python
import pytest
from uuid import uuid4
from datetime import datetime, timezone

from flashback.orchestrator.steps.retrieve import retrieve
from flashback.retrieval.schema import MomentResult


class _FakeRetrieval:
    def __init__(self, moments, linked):
        self._moments = moments
        self._linked = linked
        self.linked_calls = []

    async def search_moments(self, *, query, person_id, current_user_id):
        return self._moments

    async def search_entities(self, *, query, person_id):
        return []

    async def get_same_event_linked_moments(self, person_id, moment_ids):
        self.linked_calls.append(list(moment_ids))
        return self._linked


def _m(title):
    return MomentResult(
        id=uuid4(), person_id=uuid4(), title=title, narrative="n",
        time_anchor=None, life_period_estimate=None, sensory_details=None,
        emotional_tone=None, contributor_perspective=None,
        created_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_recall_populates_linked_account_moments():
    from types import SimpleNamespace
    base = _m("base")
    linked = _m("linked")
    deps = SimpleNamespace(retrieval=_FakeRetrieval([base], [linked]))
    state = SimpleNamespace(
        effective_intent="recall", user_message="q",
        person_id=uuid4(), user_id=uuid4(),
        related_moments=[], related_entities=[], related_threads=[],
        linked_account_moments=[],
    )
    await retrieve(state, deps)
    assert [m.title for m in state.linked_account_moments] == ["linked"]
    assert deps.retrieval.linked_calls == [[base.id]]
```

> If `retrieve` reads other attributes on `state`/`deps`, extend the `SimpleNamespace`s to satisfy them. Keep the test no-DB.

- [ ] **Step 4: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/orchestrator/test_linked_accounts_retrieval.py`
Expected: FAIL (`linked_account_moments` stays empty / attribute missing).

- [ ] **Step 5: Populate in the retrieve step**

In `retrieve.py`, inside the `if intent == "recall":` branch, after the `asyncio.gather` assigns `state.related_moments`:

```python
                if state.related_moments:
                    try:
                        state.linked_account_moments = (
                            await deps.retrieval.get_same_event_linked_moments(
                                state.person_id,
                                [m.id for m in state.related_moments],
                            )
                        )
                    except Exception as exc:  # best-effort; never block the turn
                        log.info("retrieval.linked_accounts_failed", error=str(exc))
                        state.linked_account_moments = []
```

- [ ] **Step 6: Pass into TurnContext**

In `generate_response.py`, in the `TurnContext(...)` construction after `related_moments=state.related_moments,`:

```python
        linked_account_moments=state.linked_account_moments,
```

- [ ] **Step 7: Run the test**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/orchestrator/test_linked_accounts_retrieval.py`
Expected: PASS.

- [ ] **Step 8: Checkpoint** — no commit.

---

## Task 11: Render `<linked_accounts>` + base prompt note

**Files:**
- Modify: `src/flashback/response_generator/context.py:75-102` (after `<mentioned_entities>`)
- Modify: `src/flashback/response_generator/prompts.py` (BASE_SYSTEM_PROMPT note)
- Test: `tests/response_generator/test_attribution_render.py` (extend)
- Test: `tests/response_generator/test_prompts.py` (extend)

**Interfaces:**
- Consumes: `TurnContext.linked_account_moments` (Task 10).
- Produces: a `<linked_accounts>` block rendered only when non-empty; each line `- {title}: {narrative}` + cross-contributor attribution (`told_by="…"`/`relationship="…"`) using the SAME guard as `<moments>` (`current_user_id` set AND `told_by_user_id` differs AND display name present). BASE_SYSTEM_PROMPT mentions `linked_accounts`.

- [ ] **Step 1: Write the failing render tests**

Add to `tests/response_generator/test_attribution_render.py`:

```python
def _ctx_linked(current_user_id, linked):
    return TurnContext(
        person_name="Lakshmi", intent="recall", emotional_temperature="medium",
        current_user_id=current_user_id, linked_account_moments=linked,
    )


def test_linked_account_cross_contributor_is_attributed():
    me, other = uuid4(), uuid4()
    m = _moment(told_by_user_id=other, told_by_display_name="Ravi", title="Birthday")
    rendered = render_turn_context(_ctx_linked(me, [m]))
    assert "<linked_accounts>" in rendered
    assert 'told_by="Ravi"' in rendered


def test_linked_account_own_not_attributed():
    me = uuid4()
    m = _moment(told_by_user_id=me, told_by_display_name="Priya", title="Birthday")
    rendered = render_turn_context(_ctx_linked(me, [m]))
    assert "<linked_accounts>" in rendered
    assert "told_by=" not in rendered


def test_no_linked_accounts_block_when_empty():
    me = uuid4()
    rendered = render_turn_context(_ctx_linked(me, []))
    assert "<linked_accounts>" not in rendered
```

Add to `tests/response_generator/test_prompts.py`:

```python
def test_base_prompt_mentions_linked_accounts():
    assert "linked_accounts" in prompts.BASE_SYSTEM_PROMPT
    for prompt in prompts.INTENT_TO_PROMPT.values():
        assert "linked_accounts" in prompt
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/response_generator/test_attribution_render.py tests/response_generator/test_prompts.py -k "linked"`
Expected: FAIL.

- [ ] **Step 3: Render the block**

In `context.py`, after the `<mentioned_entities>` block (after line 102) and before the `seeded_question` block:

```python
    if ctx.linked_account_moments:
        lines = []
        for moment in ctx.linked_account_moments:
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
            lines.append(
                f"- {xml_text(moment.title)}: {xml_text(moment.narrative)}{attribution}"
            )
        sections.append(_block("linked_accounts", "\n".join(lines)))
```

- [ ] **Step 4: Add the base-prompt note**

In `prompts.py`, append to the `BASE_SYSTEM_PROMPT` body a `_LINKED_ACCOUNTS_NOTE` constant in the same style as `_MENTIONED_ENTITY_ATTRIBUTION_NOTE`, concatenated where that note is added:

```python
_LINKED_ACCOUNTS_NOTE = """

SAME-EVENT ACCOUNTS: A <linked_accounts> block lists other moments about the \
SAME event the user is recalling. You may naturally weave in that another \
contributor remembers the same occasion — and when a line carries told_by="…" \
(optionally relationship="…"), credit them by name ("your brother remembers \
this day too"). Never force it, never contradict the user, and never treat a \
linked account as a correction."""
```

Concatenate `_LINKED_ACCOUNTS_NOTE` onto `BASE_SYSTEM_PROMPT` exactly where `_TAP_PENDING_NOTE` / `_MENTIONED_ENTITY_ATTRIBUTION_NOTE` are appended (so every intent prompt inherits it).

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/response_generator/`
Expected: PASS (all response-generator tests, including the pre-existing ones).

- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 12: HTTP endpoints

**Files:**
- Create: `src/flashback/http/routes/moment_links.py`
- Modify: `src/flashback/http/app.py:31-43, 243-255` (import + register two routers)
- Test: `tests/http/test_moment_links_routes.py` (new)

**Interfaces:**
- Consumes: Task 4 async helpers + Task 2 schema models.
- Produces: `event_links_router` (prefix `/event_links`) with `GET /` , `POST /{id}/acknowledge`, `POST /{id}/unlink`; `contradictions_router` (prefix `/contradictions`) with `GET /`, `POST /{id}/dismiss`. Both `Depends(require_service_token)`.

- [ ] **Step 1: Write the route module**

Create `src/flashback/http/routes/moment_links.py` (mirror `identity_merges.py` structure):

```python
"""SP5 same-event link + contradiction review endpoints (Node-driven)."""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool
from flashback.moment_links import ContradictionItem, SameEventLink
from flashback.moment_links.repository import (
    acknowledge_event_link_async,
    dismiss_contradiction_async,
    list_contradictions_async,
    list_event_links_async,
    unlink_event_link_async,
)

log = structlog.get_logger("flashback.http.moment_links")

event_links_router = APIRouter(
    prefix="/event_links", dependencies=[Depends(require_service_token)]
)
contradictions_router = APIRouter(
    prefix="/contradictions", dependencies=[Depends(require_service_token)]
)


@event_links_router.get("", response_model=list[SameEventLink])
async def list_event_links(
    person_id: UUID,
    include_acknowledged: bool = False,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> list[SameEventLink]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            return await list_event_links_async(
                cur, person_id=str(person_id),
                include_acknowledged=include_acknowledged,
            )


@event_links_router.post("/{link_id}/acknowledge")
async def acknowledge_event_link(
    link_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> dict:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                ok = await acknowledge_event_link_async(cur, link_id=str(link_id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "active link not found")
    return {"link_id": str(link_id), "acknowledged": True}


@event_links_router.post("/{link_id}/unlink")
async def unlink_event_link(
    link_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> dict:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                ok = await unlink_event_link_async(cur, link_id=str(link_id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "active link not found")
    return {"link_id": str(link_id), "unlinked": True}


@contradictions_router.get("", response_model=list[ContradictionItem])
async def list_contradictions(
    person_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> list[ContradictionItem]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            return await list_contradictions_async(cur, person_id=str(person_id))


@contradictions_router.post("/{item_id}/dismiss")
async def dismiss_contradiction(
    item_id: UUID, db_pool: AsyncConnectionPool = Depends(get_db_pool)
) -> dict:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                ok = await dismiss_contradiction_async(cur, item_id=str(item_id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pending contradiction not found")
    return {"item_id": str(item_id), "dismissed": True}
```

- [ ] **Step 2: Register the routers**

In `app.py`, add imports near line 33:

```python
from flashback.http.routes.moment_links import (
    contradictions_router,
    event_links_router,
)
```

And after `app.include_router(identity_merges_router)` (line 249):

```python
    app.include_router(event_links_router)
    app.include_router(contradictions_router)
```

- [ ] **Step 3: Write the failing endpoint test**

Create `tests/http/test_moment_links_routes.py`. Follow the existing DB-gated HTTP test pattern in `tests/http/` (the `async_db_pool` fixture + the app `TestClient`/`httpx.AsyncClient` fixture used by, e.g., `tests/http/test_identity_merges*`). Cover:
- `GET /event_links?person_id=…` returns an inserted active link (seed via sync SQL).
- `POST /event_links/{id}/acknowledge` → 200, then the link is absent from the default feed.
- `POST /event_links/{id}/unlink` → 200; second call → 404.
- `GET /contradictions?person_id=…` returns a pending item; `POST /contradictions/{id}/dismiss` → 200; second → 404.

Reuse the auth header/fixtures the other route tests use (service token). If those tests use a shared `client` fixture in `tests/http/conftest.py`, use it.

- [ ] **Step 4: Run to verify it fails, then passes after implementation**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/http/test_moment_links_routes.py`
Expected: PASS (DB up) / SKIP (no DB). If the app fails to import (router registration typo), fix before proceeding.

- [ ] **Step 5: Checkpoint** — no commit.

---

## Task 13: Docs — CLAUDE.md invariant #28, API.md, NODE_INTEGRATION.md

**Files:**
- Modify: `CLAUDE.md` (add invariant #28; note the two tables in §5; note the `moment_links` module)
- Modify: `API.md` (the 5 endpoints + request/response shapes)
- Modify: `NODE_INTEGRATION.md` (agent-owned tables Node reads via endpoints, never writes)
- Test: none (docs) — verified by review.

- [ ] **Step 1: Add invariant #28 to CLAUDE.md**

After invariant #27, add:

```
28. **Same-event linking + contradiction review are detected live, recorded,
    and provenance-resolved at read time.** The compatibility LLM gains a 4th
    verdict `same_event` alongside `refinement`/`contradiction`/`independent`.
    On the per-new-moment refinement search: `same_event` → auto-write an
    active `moment_same_event_links` row (notify + reversible `unlink`);
    `contradiction` → write a pending `moment_contradictions` row (replacing
    the old log-only path; resolution is non-destructive `dismiss` only this
    cycle — both moments always coexist). Records store moment ids only;
    `told_by_*` is resolved LIVE via JOIN to `moments` at read time (never
    snapshotted), so supersession (which moves the active row's teller, D4#4)
    never staleness-poisons attribution. Supersession repoints active links /
    pending contradictions from the old id to the new id and re-canonicalizes
    A/B order (extends #5); a repoint that would self-pair collapses the row
    (`unlinked`/`dismissed`). Same-event links feed `recall` retrieval into
    `<linked_accounts>` (cross-contributor framing via the #20/#26 attribution
    guard); contradictions never reach the agent. Endpoints: `GET /event_links`,
    `POST /event_links/{id}/acknowledge|unlink`, `GET /contradictions`,
    `POST /contradictions/{id}/dismiss`.
```

In §5, under the table list, add a one-line entry for `moment_same_event_links`
and `moment_contradictions` (agent-owned; Node reads via endpoints, never
writes). Mention migration 0033.

- [ ] **Step 2: Document the endpoints in API.md**

Add request/response shapes for the 5 endpoints, matching the route signatures (query param `person_id`/`include_acknowledged`; path id; response models `SameEventLink`/`ContradictionItem` with the JOIN-populated `told_by_*`/title fields; the `{...: true}` action responses; 404 conditions).

- [ ] **Step 3: NODE_INTEGRATION.md note**

State that `moment_same_event_links` and `moment_contradictions` are agent-owned tables; Node consumes them ONLY via the SP5 endpoints (it never writes them directly), mirroring the identity_merges contract. Same-event linking is automatic; contradiction resolution is `dismiss`-only in this cycle.

- [ ] **Step 4: Checkpoint** — no commit. Verify the three docs read consistently with the code (endpoint names, statuses, table columns).

---

## Final verification

- [ ] **Run the full suite:** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
- [ ] Confirm: no NEW failures vs the baseline (~14 no-DB / ~24-28 DB-gated pre-existing). The new SP5 test files pass (DB up) or skip (no DB). The updated contradiction-logging test passes.
- [ ] Confirm: no `git commit`/`add`/`checkout` was ever run — all changes sit in the working tree for the user.
- [ ] Report a short summary: tasks completed, new files, test counts, and the standing no-commit status.

---

## Self-Review (completed by plan author)

**Spec coverage:** D1 verdict→Task 5/6; D2 auto-link+notify+unlink→Task 2/4/7/12; D3 contradiction record+dismiss→Task 2/4/7/12; D4 agent uses links only→Task 9/10/11 (+ contradictions never in prompt, enforced by absence); D5 live provenance→Task 4/9 JOINs (no told_by columns in Task 1 schema); D6 supersession repoint→Task 3/8; migration 0033→Task 1; module→Task 2-4; endpoints→Task 12; invariant #28 + docs→Task 13. All spec sections map to a task.

**Placeholder scan:** The only `...` are in DB-test skeletons (Task 7/8/12) that explicitly defer to existing in-repo fixtures/patterns the implementer must follow — each names the exact fixture/pattern and the exact assertion. No "TBD"/"handle edge cases"/"add validation" left.

**Type consistency:** `MomentDecision.same_event_ids` (Task 6) consumed in Task 7; `insert_same_event_link`/`insert_contradiction`/`repoint_records_on_supersession` signatures consistent Task 2→3→7→8; `SameEventLink`/`ContradictionItem` fields consistent Task 2→4→12; `get_same_event_linked_moments(person_id, moment_ids)` consistent Task 9→10; `TurnContext.linked_account_moments` / `TurnState.linked_account_moments` consistent Task 10→11. Verdict literal order identical in schema + tool enum (Task 5).
