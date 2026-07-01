# Cross-Contributor Identity Merges (SP6b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make entity merges preserve the first introducer's `told_by_user_id` (and restore it on unmerge), and surface cross-contributor merges with a flag + both contributor display names.

**Architecture:** Extend the existing `flashback.identity_merges` module. `_merge_entity_rows` reads `told_by_user_id` + `created_at` for both entities and sets the survivor to the earliest introducer's `told_by` (snapshotting both originals for unmerge). The scanner captures both originals onto the `identity_merge_suggestions` row at creation (migration 0035); the read surfaces resolve display names live via `collaborator_onboarding` and expose `cross_contributor`. Detection is unchanged.

**Tech Stack:** Python, Postgres (psycopg async), FastAPI, pydantic, pytest (asyncio_mode=auto).

## Global Constraints

- **NO GIT COMMITS.** Standing user rule: never run `git commit`/`add`/`checkout`. Work stays in the working tree on `feature/collaborator-provenance`. "Checkpoint" steps are stop-and-verify, not commits.
- **Test command:** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings <path>`; DB-gated tests need `TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test`. `tests/conftest.py` `schema_applied` applies all `migrations/*.up.sql` (incl. 0035).
- **Detection is UNCHANGED** — name/alias, person-scoped. No different-surface-form detection; no verifier/disposition change.
- **Survivor provenance = earliest introducer.** On merge, survivor `told_by_user_id` = the older (by `created_at`) entity's `told_by_user_id`. On a `created_at` tie, survivor keeps its own (no rewrite). Creator-era `NULL` is a valid value.
- **cross_contributor** = `source_told_by_user_id IS DISTINCT FROM target_told_by_user_id` (NULL vs non-null → true).
- Migration number is **0035** (latest existing is 0034).

---

## File Structure

**New:** `migrations/0035_identity_merge_provenance.{up,down}.sql`; tests under `tests/identity_merges/` (+ `tests/workers/extraction/` already has merge tests — keep there if that's where existing ones live).

**Modified:**
- `src/flashback/identity_merges/repository.py` — `_merge_entity_rows` (survivor told_by + snapshot), `unmerge_async` (restore), `auto_merge_async` (store told_by), `list_suggestions_async` + `list_auto_merged_async` (cross_contributor + names).
- `src/flashback/identity_merges/scanner.py` — `_find_candidates` SELECT, `IdentityMergeCandidate`, `_orient_candidate`, `_insert_scanner_suggestion`, the `auto_merge_async` call site.
- `src/flashback/identity_merges/schema.py` — `IdentityMergeSuggestion` + `AutoMergeNotification` fields.
- Docs: `CLAUDE.md`, `API.md`, `NODE_INTEGRATION.md`, `docs/COLLABORATOR_NODE_INTEGRATION.md`.

**Where existing merge tests live:** check `tests/workers/extraction/test_identity_merge_suggestions.py` and any `tests/identity_merges/` or `tests/http/test_identity_merges*`. Add new tests beside the existing ones; create `tests/identity_merges/` with an `__init__.py` + a `conftest.py` autouse `schema_applied` trigger (mirror `tests/collaborators/conftest.py`) if no DB-fixture path already covers the chosen location.

---

## Task 1: Migration 0035 — provenance columns

**Files:**
- Create: `migrations/0035_identity_merge_provenance.up.sql` / `.down.sql`
- Test: `tests/identity_merges/test_migration.py` (+ `__init__.py`, + `conftest.py` per above)

**Interfaces:**
- Produces: `identity_merge_suggestions.source_told_by_user_id UUID NULL`, `target_told_by_user_id UUID NULL`.

- [ ] **Step 1: up migration**

`migrations/0035_identity_merge_provenance.up.sql`:

```sql
-- SP6b: capture both merged entities' first-introducer provenance on the
-- suggestion/auto-merge record at creation time, so cross-contributor merges
-- can be surfaced (the survivor's told_by is rewritten at merge time and the
-- original pair is otherwise unrecoverable). Nullable; NULL = creator era.
ALTER TABLE identity_merge_suggestions
    ADD COLUMN source_told_by_user_id UUID,
    ADD COLUMN target_told_by_user_id UUID;
```

- [ ] **Step 2: down migration**

`migrations/0035_identity_merge_provenance.down.sql`:

```sql
ALTER TABLE identity_merge_suggestions
    DROP COLUMN source_told_by_user_id,
    DROP COLUMN target_told_by_user_id;
```

- [ ] **Step 3: failing test**

`tests/identity_merges/__init__.py` (empty); `tests/identity_merges/conftest.py`:

```python
import pytest


@pytest.fixture(autouse=True)
def _schema(schema_applied):
    return schema_applied
```

`tests/identity_merges/test_migration.py`:

```python
import os
import psycopg
import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
def test_provenance_columns_exist():
    conn = psycopg.connect(_DB, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'identity_merge_suggestions'
               AND column_name IN ('source_told_by_user_id', 'target_told_by_user_id')
            """
        )
        cols = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    assert cols == {"source_told_by_user_id", "target_told_by_user_id"}
```

- [ ] **Step 4: run** — `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings tests/identity_merges/test_migration.py` → PASS (DB) / SKIP (no DB).
- [ ] **Step 5: Checkpoint** — no commit.

---

## Task 2: Survivor keeps earliest-introducer provenance + unmerge restores

**Files:**
- Modify: `src/flashback/identity_merges/repository.py` (`_merge_entity_rows`, `unmerge_async`)
- Test: `tests/identity_merges/test_merge_provenance.py`

**Interfaces:**
- Consumes: nothing new (reads `told_by_user_id` + `created_at` from `entities`).
- Produces: after any merge, the survivor's `told_by_user_id` = the older entity's; `undo_snapshot` gains `source_told_by_user_id` + `survivor_prior_told_by_user_id`; `unmerge_async` restores both.

- [ ] **Step 1: failing test**

`tests/identity_merges/test_merge_provenance.py`:

```python
import os
import uuid
import psycopg
import pytest

from flashback.identity_merges.repository import auto_merge_async, unmerge_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _person(cur):
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    return cur.fetchone()[0]


def _entity(cur, pid, name, told_by, created_at):
    cur.execute(
        "INSERT INTO entities (person_id, kind, name, description, aliases, status, "
        "told_by_user_id, created_at) "
        "VALUES (%s, 'person', %s, 'd', '{}', 'active', %s, %s) RETURNING id::text",
        (pid, name, told_by, created_at),
    )
    return cur.fetchone()[0]


async def _auto_merge(pid, source_id, target_id):
    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await auto_merge_async(
                    cur, person_id=pid, source_id=source_id, target_id=target_id,
                    proposed_alias=None, confidence="high",
                    notification_text="same person",
                    push_embedding=None, embedding_model="m", embedding_model_version="v",
                )
    finally:
        await conn.close()


@db_only
async def test_survivor_takes_older_entitys_told_by():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    pid = _person(cur)
    # Priya's "Amma" is OLDER; Ravi's "Amma" is newer and is the survivor (target).
    older = _entity(cur, pid, "Amma", priya, "2026-01-01")
    newer = _entity(cur, pid, "Amma", ravi, "2026-03-01")
    conn.close()

    await _auto_merge(pid, source_id=older, target_id=newer)

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("SELECT told_by_user_id::text, status FROM entities WHERE id=%s", (newer,))
    survivor_told_by, status = cur.fetchone()
    conn.close()
    assert status == "active"
    assert survivor_told_by == priya   # earliest introducer wins


@db_only
async def test_unmerge_restores_both_told_by():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    pid = _person(cur)
    older = _entity(cur, pid, "Amma", priya, "2026-01-01")   # source
    newer = _entity(cur, pid, "Amma", ravi, "2026-03-01")    # survivor
    conn.close()
    sug_id = await _auto_merge(pid, source_id=older, target_id=newer)

    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                res = await unmerge_async(
                    cur, suggestion_id=sug_id, push_embedding=None,
                    embedding_model="m", embedding_model_version="v",
                )
    finally:
        await conn.close()
    assert res is not None

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    # survivor reverts to its pre-merge told_by (Ravi); resurrected source keeps Priya.
    cur.execute("SELECT told_by_user_id::text FROM entities WHERE id=%s", (newer,))
    assert cur.fetchone()[0] == ravi
    cur.execute(
        "SELECT told_by_user_id::text FROM entities WHERE id=%s::uuid",
        (str(res.resurrected_entity_id),),
    )
    assert cur.fetchone()[0] == priya
    conn.close()
```

- [ ] **Step 2: run to verify it fails** — survivor stays `ravi` (no provenance logic yet).

- [ ] **Step 3: implement in `_merge_entity_rows`**

Extend the source SELECT to also fetch `told_by_user_id, created_at`:

```python
    await cursor.execute(
        """
        SELECT kind, name, description, aliases, attributes, generation_prompt,
               told_by_user_id::text, created_at
          FROM entities
         WHERE id = %s AND person_id = %s AND status = 'active'
         FOR UPDATE
        """,
        (source_id, person_id),
    )
    source = await cursor.fetchone()
```

Extend the target SELECT similarly:

```python
    await cursor.execute(
        """
        SELECT name, description, aliases, told_by_user_id::text, created_at
          FROM entities
         WHERE id = %s AND person_id = %s AND status = 'active'
         FOR UPDATE
        """,
        (target_id, person_id),
    )
    target = await cursor.fetchone()
    if source is None or target is None:
        raise ValueError("source and target entities must both be active")

    (source_kind, source_name, source_description, source_aliases,
     source_attributes, source_generation_prompt,
     source_told_by, source_created_at) = source
    target_name, target_description, target_aliases, target_told_by, target_created_at = target
```

After the existing survivor UPDATE (aliases/description/embedding NULLs), set the survivor's provenance to the earliest introducer (older `created_at`); on a tie keep the survivor's own:

```python
    survivor_told_by = target_told_by
    if source_created_at < target_created_at:
        survivor_told_by = source_told_by
    if survivor_told_by != target_told_by:
        await cursor.execute(
            "UPDATE entities SET told_by_user_id = %s WHERE id = %s",
            (survivor_told_by, target_id),
        )
```

Extend the returned snapshot dict with the two provenance values:

```python
    return {
        "source_row": {
            "person_id": person_id,
            "kind": source_kind,
            "name": source_name,
            "description": source_description,
            "aliases": list(source_aliases or []),
            "attributes": source_attributes or {},
            "generation_prompt": source_generation_prompt,
        },
        "source_told_by_user_id": source_told_by,
        "survivor_prior_told_by_user_id": target_told_by,
        "repointed_edge_ids": repointed_ids,
        "deleted_edges": deleted_edges,
    }
```

- [ ] **Step 4: implement restore in `unmerge_async`**

The resurrect INSERT must restore the source's `told_by_user_id` from the snapshot; add the column + value:

```python
    await cursor.execute(
        """
        INSERT INTO entities
              (person_id, kind, name, description, aliases, attributes,
               generation_prompt, told_by_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            person_id,
            source_row.get("kind"),
            source_row.get("name"),
            source_row.get("description"),
            source_row.get("aliases") or [],
            Json(source_row.get("attributes") or {}),
            source_row.get("generation_prompt"),
            snapshot.get("source_told_by_user_id"),
        ),
    )
```

(Use the existing `Json` import + the existing positional shape; the only change is the added `told_by_user_id` column + the snapshot value.) Then revert the survivor's provenance to its pre-merge value:

```python
    await cursor.execute(
        "UPDATE entities SET told_by_user_id = %s WHERE id = %s",
        (snapshot.get("survivor_prior_told_by_user_id"), target_id),
    )
```

Place this near where the survivor (`target_id`) edges are restored, inside the same transaction.

- [ ] **Step 5: run** — `tests/identity_merges/test_merge_provenance.py` PASS (DB) / SKIP.
- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 3: Capture both originals on the suggestion row

**Files:**
- Modify: `src/flashback/identity_merges/scanner.py` (`_find_candidates`, `IdentityMergeCandidate`, `_orient_candidate`, `_insert_scanner_suggestion`, `auto_merge_async` call)
- Modify: `src/flashback/identity_merges/repository.py` (`auto_merge_async` INSERT)
- Test: `tests/identity_merges/test_capture_provenance.py`

**Interfaces:**
- Produces: `IdentityMergeCandidate.source_told_by_user_id: str | None`, `.target_told_by_user_id: str | None`; both written to `identity_merge_suggestions.{source,target}_told_by_user_id` for scanner suggestions AND auto-merges.

- [ ] **Step 1: failing test** (`tests/identity_merges/test_capture_provenance.py`)

```python
import os
import uuid
import psycopg
import pytest

from flashback.identity_merges.repository import auto_merge_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_auto_merge_stores_both_told_by():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('S') RETURNING id::text")
    pid = cur.fetchone()[0]
    def ent(name, tb, ts):
        cur.execute(
            "INSERT INTO entities (person_id, kind, name, status, told_by_user_id, created_at) "
            "VALUES (%s,'person',%s,'active',%s,%s) RETURNING id::text", (pid, name, tb, ts))
        return cur.fetchone()[0]
    src = ent("Amma", priya, "2026-01-01"); tgt = ent("Amma", ravi, "2026-03-01")
    conn.close()

    aconn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with aconn.transaction():
            async with aconn.cursor() as c:
                sug = await auto_merge_async(
                    c, person_id=pid, source_id=src, target_id=tgt,
                    proposed_alias=None, confidence="high", notification_text="x",
                    push_embedding=None, embedding_model="m", embedding_model_version="v",
                    source_told_by_user_id=priya, target_told_by_user_id=ravi,
                )
    finally:
        await aconn.close()

    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute(
        "SELECT source_told_by_user_id::text, target_told_by_user_id::text "
        "FROM identity_merge_suggestions WHERE id=%s", (sug,))
    s, t = cur.fetchone(); conn.close()
    assert s == priya and t == ravi
```

- [ ] **Step 2: run to verify it fails** — `auto_merge_async` has no `source_told_by_user_id` kwarg yet (TypeError).

- [ ] **Step 3: `auto_merge_async` gains the two kwargs + stores them**

Signature: add `source_told_by_user_id: str | None = None, target_told_by_user_id: str | None = None`. Change the INSERT column list + VALUES + params:

```python
    await cursor.execute(
        """
        INSERT INTO identity_merge_suggestions
              (person_id, source_entity_id, target_entity_id,
               proposed_alias, reason, source, status,
               confidence, notification_text, undo_snapshot, auto_merged_at,
               source_told_by_user_id, target_told_by_user_id)
        VALUES (%s, %s, %s, %s, %s, 'scanner', 'auto_merged',
                %s, %s, %s, now(), %s, %s)
        RETURNING id::text
        """,
        (
            person_id, source_id, target_id, proposed_alias, notification_text,
            confidence, notification_text, Json(snapshot),
            source_told_by_user_id, target_told_by_user_id,
        ),
    )
```

- [ ] **Step 4: candidate carries told_by**

In `scanner.py`, `_find_candidates` SELECT: add `a.told_by_user_id::text, b.told_by_user_id::text` (append to the SELECT list, before `embedding_distance` or after — keep column order consistent with `_orient_candidate`'s unpack). Add the two columns to the `_orient_candidate` unpack tuple. `IdentityMergeCandidate` (frozen dataclass) gains `source_told_by_user_id: str | None` and `target_told_by_user_id: str | None`. In `_orient_candidate`, when assigning `source`/`target` by orientation, set the matching told_by:
- `same_name` / detail-based orientation → map a/b told_by onto source/target by which id became source vs target.
- alias-based branches → same mapping.

Concretely, capture `a_told_by, b_told_by` from the row, and wherever the function decides `source_id == a_id` vs `b_id`, set `source_told_by_user_id`/`target_told_by_user_id` to match. Return them on the `IdentityMergeCandidate(...)`.

- [ ] **Step 5: scanner insert + auto-merge call pass told_by**

`_insert_scanner_suggestion`: add `source_told_by_user_id, target_told_by_user_id` to the INSERT column list + the `SELECT %s,%s,…` values + the params tuple (from `candidate.source_told_by_user_id` / `.target_told_by_user_id`).

In the scanner's `auto_merge_async(...)` call (scanner.py ~line 95), pass `source_told_by_user_id=candidate.source_told_by_user_id, target_told_by_user_id=candidate.target_told_by_user_id`.

- [ ] **Step 6: run** — `tests/identity_merges/test_capture_provenance.py` PASS (DB) / SKIP. Also run the existing identity-merge suite to confirm the candidate/orient changes didn't break detection:
`.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/workers/extraction/test_identity_merge_suggestions.py` (and any `tests/identity_merges/` / `tests/http/test_identity_merges*`).
- [ ] **Step 7: Checkpoint** — no commit.

---

## Task 4: Surface cross_contributor + display names

**Files:**
- Modify: `src/flashback/identity_merges/schema.py` (`IdentityMergeSuggestion`, `AutoMergeNotification`)
- Modify: `src/flashback/identity_merges/repository.py` (`list_suggestions_async`, `list_auto_merged_async`)
- Test: `tests/identity_merges/test_surfacing.py`

**Interfaces:**
- Produces: both models gain `cross_contributor: bool = False`, `source_told_by_display_name: str | None = None`, `target_told_by_display_name: str | None = None`, populated by the list queries.

- [ ] **Step 1: schema fields**

In `IdentityMergeSuggestion` and `AutoMergeNotification` add:

```python
    cross_contributor: bool = False
    source_told_by_display_name: str | None = None
    target_told_by_display_name: str | None = None
```

- [ ] **Step 2: failing test** (`tests/identity_merges/test_surfacing.py`)

```python
import os
import uuid
import psycopg
import pytest

from flashback.identity_merges.repository import auto_merge_async, list_auto_merged_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_auto_merge_feed_exposes_cross_contributor_and_names():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True); cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('S') RETURNING id::text")
    pid = cur.fetchone()[0]
    for u, nm in ((priya, "Priya"), (ravi, "Ravi")):
        cur.execute(
            "INSERT INTO collaborator_onboarding "
            "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
            "VALUES (%s,%s,'rel',now(),%s,'active')", (pid, u, nm))
    def ent(name, tb, ts):
        cur.execute("INSERT INTO entities (person_id,kind,name,status,told_by_user_id,created_at) "
                    "VALUES (%s,'person',%s,'active',%s,%s) RETURNING id::text", (pid,name,tb,ts))
        return cur.fetchone()[0]
    src = ent("Amma", priya, "2026-01-01"); tgt = ent("Amma", ravi, "2026-03-01")
    conn.close()

    aconn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with aconn.transaction():
            async with aconn.cursor() as c:
                await auto_merge_async(
                    c, person_id=pid, source_id=src, target_id=tgt, proposed_alias=None,
                    confidence="high", notification_text="x", push_embedding=None,
                    embedding_model="m", embedding_model_version="v",
                    source_told_by_user_id=priya, target_told_by_user_id=ravi)
        async with aconn.cursor() as c:
            feed = await list_auto_merged_async(c, person_id=pid)
    finally:
        await aconn.close()

    assert len(feed) == 1
    item = feed[0]
    assert item.cross_contributor is True
    assert {item.source_told_by_display_name, item.target_told_by_display_name} == {"Priya", "Ravi"}
```

- [ ] **Step 3: run to verify it fails** — model lacks the fields / query doesn't populate.

- [ ] **Step 4: update the two list queries**

`list_auto_merged_async` SQL — select the stored told_by, LEFT JOIN `collaborator_onboarding` twice for names, compute `cross_contributor`:

```sql
        SELECT s.id, s.person_id,
               s.source_entity_id, s.target_entity_id, tgt.name,
               s.notification_text, s.confidence, s.acknowledged, s.auto_merged_at,
               (s.source_told_by_user_id IS DISTINCT FROM s.target_told_by_user_id) AS cross_contributor,
               cos.display_name AS source_told_by_display_name,
               cot.display_name AS target_told_by_display_name
          FROM identity_merge_suggestions s
          JOIN entities tgt ON tgt.id = s.target_entity_id
          LEFT JOIN collaborator_onboarding cos
                ON cos.person_id = s.person_id AND cos.user_id = s.source_told_by_user_id
               AND cos.status = 'active'
          LEFT JOIN collaborator_onboarding cot
                ON cot.person_id = s.person_id AND cot.user_id = s.target_told_by_user_id
               AND cot.status = 'active'
         WHERE s.person_id = %s
           AND s.status = 'auto_merged'
           {ack_filter}
         ORDER BY s.auto_merged_at DESC
```

Map the three new columns into `AutoMergeNotification(...)`. Apply the same JOIN + fields to `list_suggestions_async` (which already JOINs `entities src`/`tgt`), mapping into `IdentityMergeSuggestion(...)`.

- [ ] **Step 5: run** — `tests/identity_merges/test_surfacing.py` PASS (DB) / SKIP.
- [ ] **Step 6: Checkpoint** — no commit.

---

## Task 5: Docs

**Files:** `CLAUDE.md`, `API.md`, `NODE_INTEGRATION.md`, `docs/COLLABORATOR_NODE_INTEGRATION.md`

- [ ] **Step 1: CLAUDE.md** — under invariant #17 (or a note appended to #26), state: entity merges set the survivor's `told_by_user_id` to the earliest introducer's (older `created_at`; tie → survivor keeps own); unmerge restores both; cross-contributor merges expose `cross_contributor` + both display names (resolved live from `collaborator_onboarding`); migration 0035; detection unchanged.
- [ ] **Step 2: API.md** — add `cross_contributor`, `source_told_by_display_name`, `target_told_by_display_name` to the `/identity_merges/suggestions` + `/identity_merges/auto_merged` response shapes.
- [ ] **Step 3: NODE_INTEGRATION.md + COLLABORATOR_NODE_INTEGRATION.md** — flip SP6b from 🟡 to live; note the new fields + per-legacy audience.
- [ ] **Step 4: Checkpoint** — docs consistent with code. No commit.

---

## Final verification

- [ ] `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/identity_merges/` (+ existing merge tests under `tests/workers/extraction/` and `tests/http/`) — all pass (DB up) / skip (no DB).
- [ ] Full suite with DB up — confirm **975 + new SP6b tests pass, 0 failures** (baseline is now clean, so any failure is new).
- [ ] No `git commit`/`add`/`checkout` run — working tree only.
- [ ] Report: tasks done, new files, test counts.

---

## Self-Review (completed by plan author)

**Spec coverage:** D1 survivor provenance → Task 2 (`_merge_entity_rows`); D2 unmerge restore → Task 2 (`unmerge_async` + snapshot fields); D3 capture both originals → Task 1 (columns) + Task 3 (candidate/scanner/auto-merge); D3 surface cross_contributor + names → Task 4 (queries + schema); D4 scope (auto + approve, detection unchanged) → Task 2 (`_merge_entity_rows` serves both auto + approve), Task 3 (capture on scanner + auto paths; approve reuses the scanner-stored row); migration 0035 → Task 1; docs → Task 5. All spec sections mapped.

**Placeholder scan:** `_orient_candidate` mapping (Task 3 Step 4) is described rather than shown verbatim because the function has three orientation branches the implementer must read in-file; the rule ("set source/target told_by to match whichever id became source vs target") is exact and unambiguous. Everything else has literal code.

**Type consistency:** `auto_merge_async` gains `source_told_by_user_id`/`target_told_by_user_id` (Task 3) — used by the test (Task 3) and the scanner call site (Task 3 Step 5) and the surfacing test (Task 4); `IdentityMergeCandidate.source_told_by_user_id`/`.target_told_by_user_id` consistent scanner→insert; snapshot keys `source_told_by_user_id`/`survivor_prior_told_by_user_id` consistent between `_merge_entity_rows` (Task 2 Step 3) and `unmerge_async` (Task 2 Step 4); model fields `cross_contributor`/`source_told_by_display_name`/`target_told_by_display_name` consistent schema↔queries↔tests.
