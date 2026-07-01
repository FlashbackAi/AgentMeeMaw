# Collaborator Removal (SP6a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Node reversibly remove a contributor (hide their moments + orphaned entities via a `removed` status) and restore or fresh-start them, with no read-path/UI changes.

**Architecture:** A new `removed` status on `moments`/`entities`, leaning on the existing `active_*` views so removed content vanishes from every read path automatically. A `flashback.collaborators` module does the status flips in one transaction: hide the contributor's moments, resurrect any surviving contributor's moment they superseded (chain walk), hide entities orphaned to them, all reversible by an exact inverse. Two Node endpoints `POST /collaborators/remove|restore`.

**Tech Stack:** Python, Postgres (psycopg async cursor + transactions), FastAPI, pydantic, pytest (asyncio_mode=auto).

## Global Constraints

- **NO GIT COMMITS.** Standing user rule: never run `git commit`, `git add`, or `git checkout`. All work stays in the working tree on branch `feature/collaborator-provenance`. Every "Checkpoint" step is a stop-and-verify, NOT a commit.
- **Test command:** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings <path>`. DB-gated tests need `TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test`. The `tests/conftest.py` `schema_applied` fixture applies ALL `migrations/*.up.sql` in order, so migration 0034 is picked up automatically.
- **Reversible hide only** — removal/restore mutate **only** `status` on `collaborator_onboarding`, `moments`, `entities`. Never edges, traits, questions, profile_facts, threads, themes (spec D3, D4#1). Never DELETE.
- **`'removed'` is unique to this flow** (supersession uses `'superseded'`, merges use `'merged'`) — that is what makes restore unambiguous.
- **Ordering inside remove:** (1) onboarding → (2) moments → (3) supersession resurrection → (4) orphaned entities. Resurrection MUST run before the orphan sweep (spec E2).
- **Idempotent:** remove on already-removed / restore on already-active are no-ops returning zero counts, never errors (spec D6).
- Migration number is **0034** (latest existing is 0033). Current constraint names: `moments_status_check` (`active`,`superseded`), `entities_status_check` (`active`,`merged`).

---

## File Structure

**New files:**
- `migrations/0034_moment_entity_removed_status.up.sql` / `.down.sql`
- `src/flashback/collaborators/__init__.py` — package exports
- `src/flashback/collaborators/schema.py` — `RemovalResult`, `RestoreResult` pydantic models
- `src/flashback/collaborators/repository.py` — `remove_collaborator_async`, `restore_collaborator_async`
- `src/flashback/http/routes/collaborators.py` — `POST /collaborators/remove|restore`
- Tests: `tests/collaborators/test_remove.py`, `test_restore.py`; `tests/http/test_collaborators_routes.py`

**Modified files:**
- `src/flashback/http/models.py` — `CollaboratorActionRequest`
- `src/flashback/http/app.py` — register the router
- `CLAUDE.md`, `API.md`, `NODE_INTEGRATION.md`

---

## Task 1: Migration 0034 — `removed` status

**Files:**
- Create: `migrations/0034_moment_entity_removed_status.up.sql`
- Create: `migrations/0034_moment_entity_removed_status.down.sql`
- Test: `tests/collaborators/test_migration.py` (+ `tests/collaborators/__init__.py` empty)

**Interfaces:**
- Produces: `moments.status` accepts `'active'|'superseded'|'removed'`; `entities.status` accepts `'active'|'merged'|'removed'`. Views unchanged.

- [ ] **Step 1: Write the up migration**

`migrations/0034_moment_entity_removed_status.up.sql`:

```sql
-- SP6a: reversible collaborator removal hides moments/entities via a new
-- 'removed' status. The active_* views already filter status='active', so no
-- view changes are needed.

ALTER TABLE moments DROP CONSTRAINT moments_status_check;
ALTER TABLE moments ADD CONSTRAINT moments_status_check
    CHECK (status IN ('active', 'superseded', 'removed'));

ALTER TABLE entities DROP CONSTRAINT entities_status_check;
ALTER TABLE entities ADD CONSTRAINT entities_status_check
    CHECK (status IN ('active', 'merged', 'removed'));
```

- [ ] **Step 2: Write the down migration**

`migrations/0034_moment_entity_removed_status.down.sql`:

```sql
-- Flip any removed rows back to active before narrowing the constraint, so
-- the tightened CHECK does not fail on existing data.
UPDATE moments  SET status = 'active' WHERE status = 'removed';
UPDATE entities SET status = 'active' WHERE status = 'removed';

ALTER TABLE moments DROP CONSTRAINT moments_status_check;
ALTER TABLE moments ADD CONSTRAINT moments_status_check
    CHECK (status IN ('active', 'superseded'));

ALTER TABLE entities DROP CONSTRAINT entities_status_check;
ALTER TABLE entities ADD CONSTRAINT entities_status_check
    CHECK (status IN ('active', 'merged'));
```

- [ ] **Step 3: Write the failing test**

`tests/collaborators/__init__.py` (empty), then `tests/collaborators/test_migration.py`:

```python
import os
import psycopg
import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
def test_removed_status_allowed_on_moments_and_entities():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
        pid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO moments (person_id, title, narrative, status) "
            "VALUES (%s, 'M', 'n', 'removed') RETURNING id::text",
            (pid,),
        )
        assert cur.fetchone()[0]
        cur.execute(
            "INSERT INTO entities (person_id, kind, name, status) "
            "VALUES (%s, 'person', 'E', 'removed') RETURNING id::text",
            (pid,),
        )
        assert cur.fetchone()[0]
    finally:
        conn.close()
```

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/collaborators/test_migration.py`
Expected: PASS (schema_applied applies 0034). SKIP if no DB — note it.

- [ ] **Step 5: Checkpoint** — both tables accept `'removed'`; down migration syntactically sound. No commit.

---

## Task 2: `flashback.collaborators` — remove logic

**Files:**
- Create: `src/flashback/collaborators/__init__.py`
- Create: `src/flashback/collaborators/schema.py`
- Create: `src/flashback/collaborators/repository.py`
- Test: `tests/collaborators/test_remove.py`

**Interfaces:**
- Produces:
  - `schema.RemovalResult` (pydantic): `person_id: UUID, user_id: UUID, moments_removed: int, entities_removed: int, moments_resurrected: int`.
  - `repository.remove_collaborator_async(cursor, *, person_id: str, user_id: str) -> RemovalResult` — async cursor; caller owns the transaction. Performs spec D2 steps 1–5 in order.

- [ ] **Step 1: Write `schema.py`**

```python
"""Pydantic results for collaborator removal / restore (SP6a)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class RemovalResult(BaseModel):
    person_id: UUID
    user_id: UUID
    moments_removed: int
    entities_removed: int
    moments_resurrected: int


class RestoreResult(BaseModel):
    person_id: UUID
    user_id: UUID
    moments_restored: int
    entities_restored: int
    moments_re_superseded: int
```

- [ ] **Step 2: Write the failing test**

`tests/collaborators/test_remove.py`:

```python
import os
import psycopg
import pytest

from flashback.collaborators.repository import remove_collaborator_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _seed():
    """Person + two contributors (X, Y). Returns a dict of ids."""
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    import uuid
    X, Y = str(uuid.uuid4()), str(uuid.uuid4())
    for u, name in ((X, "Xavier"), (Y, "Yusuf")):
        cur.execute(
            "INSERT INTO collaborator_onboarding "
            "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
            "VALUES (%s, %s, 'rel', now(), %s, 'active')",
            (pid, u, name),
        )
    return conn, cur, pid, X, Y


def _moment(cur, pid, told_by, title, status="active", superseded_by=None):
    cur.execute(
        "INSERT INTO moments (person_id, title, narrative, status, told_by_user_id, told_by_display_name, superseded_by) "
        "VALUES (%s, %s, 'n', %s, %s, 'd', %s) RETURNING id::text",
        (pid, title, status, told_by, superseded_by),
    )
    return cur.fetchone()[0]


def _entity(cur, pid, told_by, name):
    cur.execute(
        "INSERT INTO entities (person_id, kind, name, status, told_by_user_id) "
        "VALUES (%s, 'person', %s, 'active', %s) RETURNING id::text",
        (pid, name, told_by),
    )
    return cur.fetchone()[0]


def _involves(cur, moment_id, entity_id):
    cur.execute(
        "INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type, status) "
        "VALUES ('moment', %s, 'entity', %s, 'involves', 'active')",
        (moment_id, entity_id),
    )


async def _remove(pid, user):
    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await remove_collaborator_async(cur, person_id=pid, user_id=user)
    finally:
        await conn.close()


@db_only
async def test_remove_hides_moments_and_orphaned_entities():
    conn, cur, pid, X, Y = _seed()
    my = _moment(cur, pid, Y, "Y moment")
    e_orphan = _entity(cur, pid, Y, "OnlyY")       # referenced only by Y's moment
    e_shared = _entity(cur, pid, Y, "Shared")       # referenced by X's moment too
    _involves(cur, my, e_orphan)
    _involves(cur, my, e_shared)
    mx = _moment(cur, pid, X, "X moment")
    _involves(cur, mx, e_shared)
    conn.close()

    result = await _remove(pid, Y)
    assert result.moments_removed == 1
    assert result.entities_removed == 1  # only OnlyY

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (my,)); assert cur.fetchone()[0] == "removed"
    cur.execute("SELECT status FROM entities WHERE id=%s", (e_orphan,)); assert cur.fetchone()[0] == "removed"
    cur.execute("SELECT status FROM entities WHERE id=%s", (e_shared,)); assert cur.fetchone()[0] == "active"
    conn.close()


@db_only
async def test_remove_resurrects_cross_contributor_superseded():
    conn, cur, pid, X, Y = _seed()
    # X created M1, Y superseded it with M2 (M1.superseded_by = M2).
    m2 = _moment(cur, pid, Y, "Y winning six")
    m1 = _moment(cur, pid, X, "X winning six", status="superseded", superseded_by=m2)
    conn.close()

    result = await _remove(pid, Y)
    assert result.moments_removed == 1
    assert result.moments_resurrected == 1

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (m2,)); assert cur.fetchone()[0] == "removed"
    cur.execute("SELECT status, superseded_by::text FROM moments WHERE id=%s", (m1,))
    st, sup = cur.fetchone()
    assert st == "active"          # X's account resurrected
    assert sup == m2               # superseded_by retained for restore
    conn.close()


@db_only
async def test_remove_is_idempotent():
    conn, cur, pid, X, Y = _seed()
    _moment(cur, pid, Y, "Y moment")
    conn.close()
    first = await _remove(pid, Y)
    assert first.moments_removed == 1
    second = await _remove(pid, Y)
    assert second.moments_removed == 0
    assert second.entities_removed == 0
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/collaborators/test_remove.py`
Expected: FAIL (`ImportError: remove_collaborator_async`).

- [ ] **Step 4: Implement `repository.py` (remove path)**

```python
"""Reversible collaborator removal + restore (SP6a).

Status flips only — never DELETE, never touch edges/traits/questions/facts.
Caller owns the transaction (async cursor).
"""

from __future__ import annotations

from .schema import RemovalResult, RestoreResult

# Hide the contributor's own active moments; return their ids.
_HIDE_MOMENTS_SQL = """
    UPDATE moments SET status = 'removed'
     WHERE person_id = %(person_id)s
       AND told_by_user_id = %(user_id)s
       AND status = 'active'
    RETURNING id::text
"""

# Resurrect the nearest surviving-contributor ancestor of each removed moment.
# Recurse past a node only when that node is the removed user's, so the walk
# stops at the first surviving contributor (spec E1).
_RESURRECT_SQL = """
    WITH RECURSIVE chain AS (
        SELECT m.id, m.superseded_by, m.told_by_user_id, m.status
          FROM moments m
         WHERE m.superseded_by = ANY(%(removed_ids)s)
        UNION ALL
        SELECT m.id, m.superseded_by, m.told_by_user_id, m.status
          FROM moments m
          JOIN chain c ON m.superseded_by = c.id
         WHERE c.told_by_user_id IS NOT DISTINCT FROM %(user_id)s
    )
    UPDATE moments SET status = 'active'
     WHERE id IN (
         SELECT id FROM chain
          WHERE told_by_user_id IS DISTINCT FROM %(user_id)s
            AND status = 'superseded'
     )
    RETURNING id::text
"""

# Hide entities introduced by the user that no surviving active moment
# references (run AFTER moments are hidden + resurrected, spec E2).
_HIDE_ORPHAN_ENTITIES_SQL = """
    UPDATE entities e SET status = 'removed'
     WHERE e.person_id = %(person_id)s
       AND e.told_by_user_id = %(user_id)s
       AND e.status = 'active'
       AND NOT EXISTS (
           SELECT 1
             FROM active_edges ed
             JOIN active_moments m ON m.id = ed.from_id
            WHERE ed.from_kind = 'moment'
              AND ed.to_kind   = 'entity'
              AND ed.to_id     = e.id
              AND ed.edge_type IN ('involves', 'happened_at')
       )
    RETURNING id::text
"""

_REMOVE_ONBOARDING_SQL = """
    UPDATE collaborator_onboarding SET status = 'removed'
     WHERE person_id = %(person_id)s AND user_id = %(user_id)s AND status = 'active'
"""


async def remove_collaborator_async(
    cursor, *, person_id: str, user_id: str
) -> RemovalResult:
    params = {"person_id": person_id, "user_id": user_id}

    # 1. onboarding row -> removed
    await cursor.execute(_REMOVE_ONBOARDING_SQL, params)

    # 2. hide the user's active moments
    await cursor.execute(_HIDE_MOMENTS_SQL, params)
    removed_ids = [r[0] for r in await cursor.fetchall()]

    # 3. resurrect nearest surviving-contributor superseded ancestors
    resurrected = 0
    if removed_ids:
        await cursor.execute(
            _RESURRECT_SQL, {"removed_ids": removed_ids, "user_id": user_id}
        )
        resurrected = len(await cursor.fetchall())

    # 4. hide orphaned entities (after resurrection)
    await cursor.execute(_HIDE_ORPHAN_ENTITIES_SQL, params)
    entities_removed = len(await cursor.fetchall())

    return RemovalResult(
        person_id=person_id,
        user_id=user_id,
        moments_removed=len(removed_ids),
        entities_removed=entities_removed,
        moments_resurrected=resurrected,
    )
```

- [ ] **Step 5: Write `__init__.py`**

```python
"""SP6a: reversible collaborator removal."""

from .repository import remove_collaborator_async, restore_collaborator_async
from .schema import RemovalResult, RestoreResult

__all__ = [
    "remove_collaborator_async",
    "restore_collaborator_async",
    "RemovalResult",
    "RestoreResult",
]
```

> `restore_collaborator_async` is added in Task 3; import it now so `__init__` is stable. If running Task 2 in isolation, temporarily drop it from the import/`__all__` and re-add in Task 3.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/collaborators/test_remove.py`
Expected: PASS (DB up) / SKIP (no DB). (`__init__` will fail to import until Task 3 adds `restore_collaborator_async` — for Task 2 in isolation, follow the note in Step 5.)

- [ ] **Step 7: Checkpoint** — no commit.

---

## Task 3: restore logic (exact inverse)

**Files:**
- Modify: `src/flashback/collaborators/repository.py`
- Test: `tests/collaborators/test_restore.py`

**Interfaces:**
- Consumes: `RestoreResult` (Task 2 schema).
- Produces: `repository.restore_collaborator_async(cursor, *, person_id: str, user_id: str) -> RestoreResult` — async cursor; caller owns the transaction. Inverse of remove (spec D4).

- [ ] **Step 1: Write the failing test**

`tests/collaborators/test_restore.py`:

```python
import os
import psycopg
import pytest

from flashback.collaborators.repository import (
    remove_collaborator_async,
    restore_collaborator_async,
)
from tests.collaborators.test_remove import _seed, _moment, _entity, _involves

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


async def _call(fn, pid, user):
    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await fn(cur, person_id=pid, user_id=user)
    finally:
        await conn.close()


@db_only
async def test_remove_then_restore_round_trips():
    conn, cur, pid, X, Y = _seed()
    my = _moment(cur, pid, Y, "Y moment")
    e = _entity(cur, pid, Y, "OnlyY")
    _involves(cur, my, e)
    conn.close()

    await _call(remove_collaborator_async, pid, Y)
    res = await _call(restore_collaborator_async, pid, Y)
    assert res.moments_restored == 1
    assert res.entities_restored == 1

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (my,)); assert cur.fetchone()[0] == "active"
    cur.execute("SELECT status FROM entities WHERE id=%s", (e,)); assert cur.fetchone()[0] == "active"
    cur.execute("SELECT status FROM collaborator_onboarding WHERE person_id=%s AND user_id=%s", (pid, Y))
    assert cur.fetchone()[0] == "active"
    conn.close()


@db_only
async def test_restore_re_supersedes_resurrected_predecessor():
    conn, cur, pid, X, Y = _seed()
    m2 = _moment(cur, pid, Y, "Y six")
    m1 = _moment(cur, pid, X, "X six", status="superseded", superseded_by=m2)
    conn.close()

    await _call(remove_collaborator_async, pid, Y)   # m2->removed, m1 resurrected->active
    res = await _call(restore_collaborator_async, pid, Y)
    assert res.moments_re_superseded == 1

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (m2,)); assert cur.fetchone()[0] == "active"
    cur.execute("SELECT status FROM moments WHERE id=%s", (m1,)); assert cur.fetchone()[0] == "superseded"
    conn.close()


@db_only
async def test_restore_when_active_is_noop():
    conn, cur, pid, X, Y = _seed()
    _moment(cur, pid, Y, "Y moment")
    conn.close()
    res = await _call(restore_collaborator_async, pid, Y)
    assert res.moments_restored == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/collaborators/test_restore.py`
Expected: FAIL (`ImportError: restore_collaborator_async`).

- [ ] **Step 3: Implement restore in `repository.py`**

Append:

```python
_RESTORE_MOMENTS_SQL = """
    UPDATE moments SET status = 'active'
     WHERE person_id = %(person_id)s
       AND told_by_user_id = %(user_id)s
       AND status = 'removed'
    RETURNING id::text
"""

_RESTORE_ENTITIES_SQL = """
    UPDATE entities SET status = 'active'
     WHERE person_id = %(person_id)s
       AND told_by_user_id = %(user_id)s
       AND status = 'removed'
    RETURNING id::text
"""

# Re-supersede predecessors that removal resurrected: an active moment whose
# superseded_by points at a just-restored moment and whose teller differs.
_RE_SUPERSEDE_SQL = """
    UPDATE moments SET status = 'superseded'
     WHERE superseded_by = ANY(%(restored_ids)s)
       AND status = 'active'
       AND told_by_user_id IS DISTINCT FROM %(user_id)s
    RETURNING id::text
"""

# Restore the onboarding row only if no active row already exists for this
# (person, user) — tolerates a re-session-under-same-id inconsistency (E10).
_RESTORE_ONBOARDING_SQL = """
    UPDATE collaborator_onboarding SET status = 'active'
     WHERE person_id = %(person_id)s AND user_id = %(user_id)s AND status = 'removed'
       AND NOT EXISTS (
           SELECT 1 FROM collaborator_onboarding
            WHERE person_id = %(person_id)s AND user_id = %(user_id)s AND status = 'active'
       )
"""


async def restore_collaborator_async(
    cursor, *, person_id: str, user_id: str
) -> RestoreResult:
    params = {"person_id": person_id, "user_id": user_id}

    await cursor.execute(_RESTORE_MOMENTS_SQL, params)
    restored_ids = [r[0] for r in await cursor.fetchall()]

    await cursor.execute(_RESTORE_ENTITIES_SQL, params)
    entities_restored = len(await cursor.fetchall())

    re_superseded = 0
    if restored_ids:
        await cursor.execute(
            _RE_SUPERSEDE_SQL, {"restored_ids": restored_ids, "user_id": user_id}
        )
        re_superseded = len(await cursor.fetchall())

    await cursor.execute(_RESTORE_ONBOARDING_SQL, params)

    return RestoreResult(
        person_id=person_id,
        user_id=user_id,
        moments_restored=len(restored_ids),
        entities_restored=entities_restored,
        moments_re_superseded=re_superseded,
    )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/collaborators/`
Expected: PASS (DB up) / SKIP (no DB). Confirm `__init__.py` imports both functions.

- [ ] **Step 5: Checkpoint** — no commit.

---

## Task 4: HTTP endpoints

**Files:**
- Modify: `src/flashback/http/models.py`
- Create: `src/flashback/http/routes/collaborators.py`
- Modify: `src/flashback/http/app.py`
- Test: `tests/http/test_collaborators_routes.py`

**Interfaces:**
- Consumes: `remove_collaborator_async`, `restore_collaborator_async`, `RemovalResult`, `RestoreResult` (Tasks 2–3).
- Produces: `CollaboratorActionRequest` (`person_id: UUID, user_id: UUID`, tolerates a stray `role_id`); `POST /collaborators/remove` → `RemovalResult`; `POST /collaborators/restore` → `RestoreResult`.

- [ ] **Step 1: Add the request model**

In `src/flashback/http/models.py`, append (do NOT set `extra='forbid'` — a legacy `role_id` must be tolerated/ignored per #26):

```python
class CollaboratorActionRequest(BaseModel):
    """Remove or restore a contributor. ``role_id`` (if sent) is ignored."""

    person_id: UUID
    user_id: UUID
```

- [ ] **Step 2: Write the route module**

`src/flashback/http/routes/collaborators.py` (mirror `moment_links.py`):

```python
"""Collaborator removal / restore endpoints (Node-driven, SP6a)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from flashback.collaborators import (
    RemovalResult,
    RestoreResult,
    remove_collaborator_async,
    restore_collaborator_async,
)
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool
from flashback.http.models import CollaboratorActionRequest

log = structlog.get_logger("flashback.http.collaborators")

router = APIRouter(
    prefix="/collaborators", dependencies=[Depends(require_service_token)]
)


@router.post("/remove", response_model=RemovalResult)
async def remove_collaborator(
    req: CollaboratorActionRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> RemovalResult:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await remove_collaborator_async(
                    cur, person_id=str(req.person_id), user_id=str(req.user_id)
                )
    log.info(
        "collaborator.removed",
        person_id=str(req.person_id),
        user_id=str(req.user_id),
        moments_removed=result.moments_removed,
        entities_removed=result.entities_removed,
        moments_resurrected=result.moments_resurrected,
    )
    return result


@router.post("/restore", response_model=RestoreResult)
async def restore_collaborator(
    req: CollaboratorActionRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> RestoreResult:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await restore_collaborator_async(
                    cur, person_id=str(req.person_id), user_id=str(req.user_id)
                )
    log.info(
        "collaborator.restored",
        person_id=str(req.person_id),
        user_id=str(req.user_id),
        moments_restored=result.moments_restored,
        entities_restored=result.entities_restored,
        moments_re_superseded=result.moments_re_superseded,
    )
    return result
```

- [ ] **Step 3: Register the router**

In `src/flashback/http/app.py`, add the import near the other route imports:

```python
from flashback.http.routes.collaborators import router as collaborators_router
```

and register it after `event_links_router`/`contradictions_router`:

```python
    app.include_router(collaborators_router)
```

- [ ] **Step 4: Verify the app imports**

Run: `.venv/Scripts/python.exe -c "import flashback.http.app as a; print('app import ok')"`
Expected: `app import ok`.

- [ ] **Step 5: Write the endpoint test**

`tests/http/test_collaborators_routes.py` (follow the `client_with_db` + `auth_headers` pattern from `tests/http/test_moment_links_routes.py`):

```python
import os
import uuid

import psycopg
import pytest

from tests.http.conftest import auth_headers

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _seed_person_user_moment():
    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    user = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO collaborator_onboarding "
        "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
        "VALUES (%s, %s, 'rel', now(), 'Y', 'active')",
        (pid, user),
    )
    cur.execute(
        "INSERT INTO moments (person_id, title, narrative, status, told_by_user_id, told_by_display_name) "
        "VALUES (%s, 'M', 'n', 'active', %s, 'Y') RETURNING id::text",
        (pid, user),
    )
    mid = cur.fetchone()[0]
    conn.close()
    return pid, user, mid


@db_only
async def test_remove_then_restore_endpoints(client_with_db):
    pid, user, mid = _seed_person_user_moment()

    r = await client_with_db.post(
        "/collaborators/remove", json={"person_id": pid, "user_id": user}, headers=auth_headers()
    )
    assert r.status_code == 200
    assert r.json()["moments_removed"] == 1

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (mid,)); assert cur.fetchone()[0] == "removed"
    conn.close()

    r2 = await client_with_db.post(
        "/collaborators/restore", json={"person_id": pid, "user_id": user}, headers=auth_headers()
    )
    assert r2.status_code == 200
    assert r2.json()["moments_restored"] == 1


@db_only
async def test_remove_unknown_user_is_zero_not_404(client_with_db):
    pid, _user, _mid = _seed_person_user_moment()
    r = await client_with_db.post(
        "/collaborators/remove",
        json={"person_id": pid, "user_id": str(uuid.uuid4())},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["moments_removed"] == 0


@db_only
async def test_role_id_is_tolerated(client_with_db):
    pid, user, _mid = _seed_person_user_moment()
    r = await client_with_db.post(
        "/collaborators/remove",
        json={"person_id": pid, "user_id": user, "role_id": str(uuid.uuid4())},
        headers=auth_headers(),
    )
    assert r.status_code == 200
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/http/test_collaborators_routes.py`
Expected: PASS (DB up) / SKIP (no DB).

- [ ] **Step 7: Checkpoint** — no commit.

---

## Task 5: Docs — CLAUDE.md #29, API.md, NODE_INTEGRATION.md

**Files:**
- Modify: `CLAUDE.md` (invariant #29 + `removed` status note in §5)
- Modify: `API.md` (the two endpoints + result shapes)
- Modify: `NODE_INTEGRATION.md` (removal is agent-owned; restore vs fresh-start)

- [ ] **Step 1: Add invariant #29 to CLAUDE.md**

After invariant #28, add:

```
29. **Collaborator removal is a reversible hide, never a delete.** `POST
    /collaborators/remove` flips `status → 'removed'` on the contributor's
    `collaborator_onboarding` row, their `moments` (`told_by_user_id`), and the
    `entities` they introduced that **no surviving active moment references**
    (orphaned-to-them; refines D4#1, which kept all entities). The `active_*`
    views make all of it vanish from every read path — no retrieval/UI change.
    Removal also **resurrects** the nearest *surviving-contributor* moment that a
    removed moment had superseded (walk `superseded_by` back, recursing only
    through the removed user's moments), so a departing contributor's retelling
    never collateral-hides another's account; `superseded_by` is retained so
    restore can re-supersede. Removal touches **only** `status` on those three
    tables — never edges/traits/questions/facts/threads/themes. `'removed'` is
    unique to this flow (vs `'superseded'`/`'merged'`), so `POST
    /collaborators/restore` is its exact inverse. Re-invite is either restore
    (same `user_id`) or fresh-start (Node issues a new `user_id`; no agent work).
    Idempotent. Module `flashback.collaborators`; migration 0034.
```

In §5, note `moments.status` now includes `'removed'` and `entities.status` includes `'removed'` (SP6a; reversible removal).

- [ ] **Step 2: API.md**

Document `POST /collaborators/remove` and `POST /collaborators/restore`: body `{person_id, user_id}` (legacy `role_id` tolerated/ignored); responses `RemovalResult` (`moments_removed, entities_removed, moments_resurrected`) and `RestoreResult` (`moments_restored, entities_restored, moments_re_superseded`); unknown `(person_id, user_id)` returns zero counts (idempotent), not 404. Add both rows to the endpoint catalogue table.

- [ ] **Step 3: NODE_INTEGRATION.md**

State that removal is agent-owned: Node calls `/collaborators/remove` to offboard a contributor (their moments + orphaned entities stop surfacing; shared entities and all other content remain), and chooses **restore** (`/collaborators/restore`, same `user_id`) or **fresh-start** (re-invite under a new `user_id`) to bring someone back. Node never writes `status` directly. Note removal should not be issued during the contributor's live session (Node owns session lifecycle).

- [ ] **Step 4: Checkpoint** — docs read consistently with the code. No commit.

---

## Final verification

- [ ] **Run the SP6a tests:** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/collaborators/ tests/http/test_collaborators_routes.py` — all pass (DB up) or skip (no DB).
- [ ] **Run the full suite with DB up** and confirm no NEW failures vs the SP5 baseline (~24 pre-existing DB-gated failures: embedding, voyage, producers/underdeveloped, phase_gate turn_switch, coverage_tap, http integration stub-fixtures). SP6a is additive.
- [ ] Confirm no `git commit`/`add`/`checkout` was run — all changes in the working tree.
- [ ] Report: tasks done, new files, test counts, no-commit status.

---

## Self-Review (completed by plan author)

**Spec coverage:** D1 `removed` status → Task 1; D2 remove steps 1–5 (onboarding/moments/resurrection/orphan-entities) → Task 2; D3 edges-untouched → enforced by Task 2 SQL touching only status; D4 restore inverse + re-supersede → Task 3; D5 re-invite (restore endpoint + fresh-start no-op) → Task 4 (`/restore`) + documented Task 5; D6 idempotency → Task 2/3 SQL (`WHERE status='active'/'removed'`) + tests; migration 0034 → Task 1; module → Tasks 2–3; endpoints → Task 4; invariant #29 + docs → Task 5. All spec sections mapped.

**Placeholder scan:** No TBD/handle-edge-cases/etc. Every code step has full SQL/Python. The one cross-test import (`test_restore` imports helpers from `test_remove`) is real and explicit.

**Type consistency:** `remove_collaborator_async`/`restore_collaborator_async(cursor, *, person_id: str, user_id: str)` consistent Task 2→3→4; `RemovalResult`/`RestoreResult` field names consistent schema→repository→routes→tests; `CollaboratorActionRequest(person_id, user_id)` consistent model→routes→tests; SQL param keys (`person_id`, `user_id`, `removed_ids`, `restored_ids`) consistent within each query.

**E2 ordering** verified: Task 2 implementation runs hide-moments → resurrect → hide-orphan-entities, and `test_remove_hides_moments_and_orphaned_entities` + the resurrection test together cover that a resurrected moment's entities are protected (orphan sweep sees them active).
