# Tribute Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the persistence + read-surface foundation for the Tribute output feature — a `tributes` table, a `tribute` theme kind, a `tribute_status` view that computes the completion checklist, plus the repository and progress-reader modules.

**Architecture:** Migration 0027 adds the `tributes` table and a `tribute_status` SQL view that derives the four completion slots (memories / message / appearance / signature) and a weighted percent directly from the existing graph (`active_moments`, `persons.ground_truth`, `active_traits`/`active_entities`) plus the tribute row's `message_text`. The weights live **only** in the view so Node (which reads the view directly) and the agent never drift. A thin Python repository handles inserts/updates; a progress reader `SELECT`s from the view and decorates the slots with display copy from a code-side checklist config.

**Tech Stack:** Python, psycopg (sync cursors, `%(name)s` params, `psycopg.types.json.Json` for JSONB), Postgres + pgvector, pytest against a `TEST_DATABASE_URL` Postgres on `:15432`.

**Scope note:** This is Plan 1 of 4 (Data foundation). Plan 2 = capture flow, Plan 3 = assembly + artifacts, Plan 4 = Father's Day skin. Async repository surfaces, the `/turn` sidecar, and the `/tributes/{id}/generate` endpoint are intentionally **out of scope here** and arrive in later plans. Spec: [`docs/superpowers/specs/2026-06-14-tribute-output-design.md`](../specs/2026-06-14-tribute-output-design.md).

**Testing convention (per user instruction):** Build first, tests after. Tasks 1–3 implement + commit with **no test run**. Task 4 authors all tests and runs the full suite once. This intentionally departs from test-first TDD.

---

## File Structure

| File | Responsibility |
|---|---|
| `migrations/0027_tributes.up.sql` (create) | `tributes` table, extend `themes.kind` to allow `tribute`, `tribute_status` view |
| `migrations/0027_tributes.down.sql` (create) | Reverse 0027 (drop view + table, restore prior `themes` kind constraints) |
| `src/flashback/tribute/__init__.py` (create) | Package marker |
| `src/flashback/tribute/repository.py` (create) | `TributeRow` dataclass + sync insert/fetch/update functions |
| `src/flashback/tribute/checklist.py` (create) | Slot display metadata (key/label/hint/weight) — single source of slot order + copy |
| `src/flashback/tribute/progress.py` (create) | `TributeProgress`/`TributeSlot` dataclasses + `fetch_tribute_progress_sync` reading the view |
| `tests/tribute/__init__.py` (create) | Test package marker |
| `tests/tribute/test_migration_0027.py` (create) | Text regression on the migration SQL |
| `tests/tribute/test_repository.py` (create) | DB round-trip for the repository |
| `tests/tribute/test_progress.py` (create) | DB test: empty → fully-filled progress against the view |
| `tests/tribute/test_checklist.py` (create) | Pure unit test on slot config |

---

### Task 1: Migration 0027 — tributes table, tribute theme kind, tribute_status view

**Files:**
- Create: `migrations/0027_tributes.up.sql`
- Create: `migrations/0027_tributes.down.sql`

- [ ] **Step 1: Write the up migration**

Create `migrations/0027_tributes.up.sql`:

```sql
-- ============================================================================
-- 0027_tributes.up.sql
-- Flashback AI: Legacy Mode  -  Tribute output layer
-- ----------------------------------------------------------------------------
-- Tribute output (design 2026-06-14): a contributor-voiced shareable tribute
-- video + a general storybook. One row per tribute output per person (NOT
-- 1:1 — a contributor may make more than one over time).
--
-- Adds:
--   * 'tribute' allowed as a themes.kind (alongside universal/emergent).
--     Tributes carry no originating thread (like universals).
--   * tributes table
--   * tribute_status view — the Node read surface. Computes the four
--     completion-checklist slots and a weighted percent from the existing
--     graph + the tribute row's message_text. WEIGHTS LIVE HERE ONLY so the
--     agent (steering, live meter) and Node never drift.
--
-- Slot definitions (mirrored as display copy in flashback/tribute/checklist.py):
--   memories   (weight 40) = >= 3 qualifying active moments for the person
--   message    (weight 30) = tribute.message_text present
--   appearance (weight 20) = ground_truth has region + (birth_era|era_span)
--                            + one of distinctive_features|attire|build
--   signature  (weight 10) = >= 1 active trait OR an active entity carrying a
--                            'saying'/'mannerism' attribute
-- 'Qualifying' moment = active AND has any of: sensory_details, time_anchor,
-- an involves edge to any entity.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- themes.kind: allow 'tribute'
-- ----------------------------------------------------------------------------
-- The 0020 inline CHECK is auto-named themes_kind_check; the thread rule is
-- the named chk_themes_kind_thread. Rebuild both to admit 'tribute'.

ALTER TABLE themes DROP CONSTRAINT IF EXISTS themes_kind_check;
ALTER TABLE themes
    ADD CONSTRAINT themes_kind_check
    CHECK (kind IN ('universal', 'emergent', 'tribute'));

ALTER TABLE themes DROP CONSTRAINT IF EXISTS chk_themes_kind_thread;
ALTER TABLE themes
    ADD CONSTRAINT chk_themes_kind_thread CHECK (
        (kind = 'emergent' AND thread_id IS NOT NULL)
        OR
        (kind IN ('universal', 'tribute') AND thread_id IS NULL)
    );

-- ----------------------------------------------------------------------------
-- tributes table
-- ----------------------------------------------------------------------------

CREATE TABLE tributes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    theme_id  UUID REFERENCES themes(id) ON DELETE SET NULL,

    message_text         TEXT,    -- polished contributor message (Plan 2)
    message_source_turns JSONB,   -- raw words it was distilled from

    script           JSONB,       -- assembled scenes/captions (Plan 3)
    scene_moment_ids  UUID[],      -- which moments became scenes (Plan 3)
    checklist_state  JSONB,        -- snapshot at assembly time (Plan 3)

    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'ready', 'generating', 'complete', 'superseded')),

    -- Node writes URLs; we only ever write prompts/context (CLAUDE.md §3).
    video_url     TEXT,
    image_url     TEXT,
    thumbnail_url TEXT,
    generation_prompt         TEXT,
    latest_generation_context JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tributes_person_idx ON tributes (person_id, status);
CREATE INDEX tributes_theme_idx  ON tributes (theme_id) WHERE theme_id IS NOT NULL;

CREATE TRIGGER trg_tributes_updated_at BEFORE UPDATE ON tributes
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ----------------------------------------------------------------------------
-- tribute_status view (Node read surface)
-- ----------------------------------------------------------------------------

CREATE VIEW tribute_status AS
SELECT
    tr.id,
    tr.person_id,
    tr.theme_id,
    tr.status,
    COALESCE(mem.qualifying_count, 0)                       AS memories_count,
    (tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0)             AS message_present,
    COALESCE(appr.appearance_present, false)                AS appearance_present,
    COALESCE(sig.signature_present, false)                  AS signature_present,
    (
        (LEAST(COALESCE(mem.qualifying_count, 0), 3)::numeric / 3 * 40)
      + (CASE WHEN tr.message_text IS NOT NULL
                AND length(btrim(tr.message_text)) > 0 THEN 30 ELSE 0 END)
      + (CASE WHEN COALESCE(appr.appearance_present, false) THEN 20 ELSE 0 END)
      + (CASE WHEN COALESCE(sig.signature_present, false) THEN 10 ELSE 0 END)
    )::int                                                  AS percent,
    (
        COALESCE(mem.qualifying_count, 0) >= 3
        AND tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0
        AND COALESCE(appr.appearance_present, false)
        AND COALESCE(sig.signature_present, false)
    )                                                       AS ready,
    tr.video_url,
    tr.image_url,
    tr.thumbnail_url,
    tr.created_at,
    tr.updated_at
FROM tributes tr
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS qualifying_count
      FROM active_moments m
     WHERE m.person_id = tr.person_id
       AND (
            m.sensory_details IS NOT NULL
         OR m.time_anchor IS NOT NULL
         OR EXISTS (
             SELECT 1 FROM edges ie
              WHERE ie.from_kind = 'moment'
                AND ie.from_id   = m.id
                AND ie.edge_type = 'involves'
                AND ie.status    = 'active'
         )
       )
) mem ON true
LEFT JOIN LATERAL (
    SELECT (
        (p.ground_truth -> 'region' ->> 'value') IS NOT NULL
        AND (
            (p.ground_truth -> 'birth_era' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'era_span' ->> 'value') IS NOT NULL
        )
        AND (
            (p.ground_truth -> 'distinctive_features' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'attire' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'build' ->> 'value') IS NOT NULL
        )
    ) AS appearance_present
      FROM persons p
     WHERE p.id = tr.person_id
) appr ON true
LEFT JOIN LATERAL (
    SELECT (
        EXISTS (
            SELECT 1 FROM active_traits tt WHERE tt.person_id = tr.person_id
        )
        OR EXISTS (
            SELECT 1 FROM active_entities en
             WHERE en.person_id = tr.person_id
               AND (en.attributes ? 'saying' OR en.attributes ? 'mannerism')
        )
    ) AS signature_present
) sig ON true
WHERE tr.status <> 'superseded';

COMMIT;
```

- [ ] **Step 2: Write the down migration**

Create `migrations/0027_tributes.down.sql`:

```sql
BEGIN;

DROP VIEW IF EXISTS tribute_status;
DROP TABLE IF EXISTS tributes;

-- Restore the pre-0027 themes constraints (universal/emergent only).
ALTER TABLE themes DROP CONSTRAINT IF EXISTS chk_themes_kind_thread;
ALTER TABLE themes
    ADD CONSTRAINT chk_themes_kind_thread CHECK (
        (kind = 'universal' AND thread_id IS NULL)
        OR
        (kind = 'emergent'  AND thread_id IS NOT NULL)
    );

ALTER TABLE themes DROP CONSTRAINT IF EXISTS themes_kind_check;
ALTER TABLE themes
    ADD CONSTRAINT themes_kind_check
    CHECK (kind IN ('universal', 'emergent'));

COMMIT;
```

- [ ] **Step 3: Commit**

```bash
git add migrations/0027_tributes.up.sql migrations/0027_tributes.down.sql
git commit -m "feat(tribute): migration 0027 — tributes table + tribute_status view

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 2: Tribute repository

**Files:**
- Create: `src/flashback/tribute/__init__.py`
- Create: `src/flashback/tribute/repository.py`

- [ ] **Step 1: Create the package marker**

Create `src/flashback/tribute/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the repository**

Create `src/flashback/tribute/repository.py`:

```python
"""Repository for the ``tributes`` table.

Sync surfaces only in Plan 1 (mirrors the project's test fixtures, which
use a sync psycopg pool). Async surfaces for the HTTP endpoint arrive in
Plan 3 alongside ``POST /tributes/{id}/generate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Json


@dataclass(frozen=True)
class TributeRow:
    id: str
    person_id: str
    theme_id: str | None
    message_text: str | None
    status: str
    video_url: str | None
    image_url: str | None
    thumbnail_url: str | None


_SELECT_TRIBUTE_COLUMNS = (
    "id::text, person_id::text, theme_id::text, message_text, status, "
    "video_url, image_url, thumbnail_url"
)


def _row_to_tribute(row) -> TributeRow:
    (
        tid,
        person_id,
        theme_id,
        message_text,
        status,
        video_url,
        image_url,
        thumbnail_url,
    ) = row
    return TributeRow(
        id=tid,
        person_id=person_id,
        theme_id=theme_id,
        message_text=message_text,
        status=status,
        video_url=video_url,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
    )


_INSERT_TRIBUTE_SQL = """
INSERT INTO tributes (person_id, theme_id, status)
VALUES (%(person_id)s, %(theme_id)s, 'draft')
RETURNING id::text
"""


def insert_tribute_sync(
    cur, *, person_id: UUID | str, theme_id: UUID | str | None = None
) -> str:
    """Insert a fresh draft tribute and return its id."""
    cur.execute(
        _INSERT_TRIBUTE_SQL,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id) if theme_id is not None else None,
        },
    )
    (tribute_id,) = cur.fetchone()
    return tribute_id


_FETCH_TRIBUTE_SQL = (
    f"SELECT {_SELECT_TRIBUTE_COLUMNS} FROM tributes WHERE id = %(id)s"
)


def fetch_tribute_sync(cur, *, tribute_id: UUID | str) -> TributeRow | None:
    """Return one tribute by id, or None."""
    cur.execute(_FETCH_TRIBUTE_SQL, {"id": str(tribute_id)})
    row = cur.fetchone()
    return _row_to_tribute(row) if row is not None else None


_SET_MESSAGE_SQL = """
UPDATE tributes
   SET message_text = %(message_text)s,
       message_source_turns = %(source_turns)s
 WHERE id = %(id)s
"""


def set_message_sync(
    cur,
    *,
    tribute_id: UUID | str,
    message_text: str,
    source_turns: list[dict[str, Any]] | None = None,
) -> None:
    """Store the polished message + the raw turns it was distilled from."""
    cur.execute(
        _SET_MESSAGE_SQL,
        {
            "id": str(tribute_id),
            "message_text": message_text,
            "source_turns": Json(source_turns) if source_turns is not None else None,
        },
    )


_SET_STATUS_SQL = "UPDATE tributes SET status = %(status)s WHERE id = %(id)s"


def set_status_sync(cur, *, tribute_id: UUID | str, status: str) -> None:
    """Advance the lifecycle status (draft/ready/generating/complete/superseded)."""
    cur.execute(_SET_STATUS_SQL, {"id": str(tribute_id), "status": status})
```

- [ ] **Step 3: Commit**

```bash
git add src/flashback/tribute/__init__.py src/flashback/tribute/repository.py
git commit -m "feat(tribute): tributes repository (sync surfaces)

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 3: Checklist config + progress reader

**Files:**
- Create: `src/flashback/tribute/checklist.py`
- Create: `src/flashback/tribute/progress.py`

- [ ] **Step 1: Write the checklist config**

Create `src/flashback/tribute/checklist.py`:

```python
"""Display metadata for the tribute completion checklist.

The FILLED / PERCENT computation lives in the ``tribute_status`` SQL view
(the surface Node reads directly), so the weights never drift between
Python and SQL. This module holds only the user-facing slot ORDER + COPY,
consumed by steering (Plan 2) and the live meter (Plan 4). The labels here
are the neutral default skin; campaign skins (Plan 4) may override
``label`` / ``hint`` per slot. ``weight`` is duplicated here purely as
documentation of the view's weighting and for steering priority — the
SQL view remains the source of truth for the actual percent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlotMeta:
    key: str
    label: str
    hint: str
    weight: int


# Order = display order = steering priority (highest weight first).
SLOTS: tuple[SlotMeta, ...] = (
    SlotMeta(
        key="memories",
        label="Shared memories",
        hint="Tell three stories about a time with them.",
        weight=40,
    ),
    SlotMeta(
        key="message",
        label="Your message",
        hint="Say one thing straight to them.",
        weight=30,
    ),
    SlotMeta(
        key="appearance",
        label="How they looked",
        hint="A few details so we can picture them.",
        weight=20,
    ),
    SlotMeta(
        key="signature",
        label="What made them them",
        hint="A saying, a habit, or a trait of theirs.",
        weight=10,
    ),
)

SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
```

- [ ] **Step 2: Write the progress reader**

Create `src/flashback/tribute/progress.py`:

```python
"""Read the tribute completion progress from the ``tribute_status`` view.

Pure read, no side effects. The view owns the filled/percent math; this
module decorates each slot with display copy from ``checklist.SLOTS`` so
internal callers (steering, assembly, the live meter) get a single typed
shape. Node reads the view directly and does not call this.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flashback.tribute.checklist import SLOTS


@dataclass(frozen=True)
class TributeSlot:
    key: str
    label: str
    hint: str
    filled: bool


@dataclass(frozen=True)
class TributeProgress:
    tribute_id: str
    percent: int
    ready: bool
    slots: list[TributeSlot]


_PROGRESS_SQL = """
SELECT memories_count, message_present, appearance_present,
       signature_present, percent, ready
  FROM tribute_status
 WHERE id = %(id)s
"""


def fetch_tribute_progress_sync(
    cur, *, tribute_id: UUID | str
) -> TributeProgress | None:
    """Return the decorated progress for one tribute, or None if absent."""
    cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = cur.fetchone()
    if row is None:
        return None
    (
        memories_count,
        message_present,
        appearance_present,
        signature_present,
        percent,
        ready,
    ) = row

    filled_by_key = {
        "memories": memories_count >= 3,
        "message": bool(message_present),
        "appearance": bool(appearance_present),
        "signature": bool(signature_present),
    }
    slots = [
        TributeSlot(key=s.key, label=s.label, hint=s.hint, filled=filled_by_key[s.key])
        for s in SLOTS
    ]
    return TributeProgress(
        tribute_id=str(tribute_id),
        percent=int(percent),
        ready=bool(ready),
        slots=slots,
    )
```

- [ ] **Step 3: Commit**

```bash
git add src/flashback/tribute/checklist.py src/flashback/tribute/progress.py
git commit -m "feat(tribute): completion checklist config + progress reader

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 4: Tests + run the suite

**Files:**
- Create: `tests/tribute/__init__.py`
- Create: `tests/tribute/test_migration_0027.py`
- Create: `tests/tribute/test_repository.py`
- Create: `tests/tribute/test_progress.py`
- Create: `tests/tribute/test_checklist.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/tribute/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the migration regression test**

Create `tests/tribute/test_migration_0027.py`:

```python
"""Text regression checks for the 0027 tributes migration."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UP = REPO_ROOT / "migrations" / "0027_tributes.up.sql"
DOWN = REPO_ROOT / "migrations" / "0027_tributes.down.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_0027_creates_tributes_table() -> None:
    sql = _sql(UP)
    assert re.search(r"CREATE\s+TABLE\s+tributes\b", sql, re.I)
    assert "message_text" in sql
    assert "message_source_turns" in sql
    assert "latest_generation_context" in sql


def test_0027_allows_tribute_theme_kind() -> None:
    sql = _sql(UP)
    assert re.search(
        r"kind\s+IN\s*\(\s*'universal'\s*,\s*'emergent'\s*,\s*'tribute'\s*\)",
        sql,
        re.I,
    )


def test_0027_creates_status_view_with_percent_and_ready() -> None:
    sql = _sql(UP)
    assert re.search(r"CREATE\s+VIEW\s+tribute_status\s+AS", sql, re.I)
    assert "percent" in sql
    assert "ready" in sql
    assert "appearance_present" in sql
    assert "signature_present" in sql


def test_0027_down_drops_view_and_table_and_restores_kind() -> None:
    sql = _sql(DOWN)
    assert re.search(r"DROP\s+VIEW\s+IF\s+EXISTS\s+tribute_status", sql, re.I)
    assert re.search(r"DROP\s+TABLE\s+IF\s+EXISTS\s+tributes", sql, re.I)
    assert re.search(
        r"kind\s+IN\s*\(\s*'universal'\s*,\s*'emergent'\s*\)", sql, re.I
    )
```

- [ ] **Step 3: Write the repository DB test**

Create `tests/tribute/test_repository.py`:

```python
"""DB round-trip tests for the tributes repository."""

from __future__ import annotations

from flashback.tribute.repository import (
    fetch_tribute_sync,
    insert_tribute_sync,
    set_message_sync,
    set_status_sync,
)


def test_insert_and_fetch_draft_tribute(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            conn.commit()
            row = fetch_tribute_sync(cur, tribute_id=tribute_id)

    assert row is not None
    assert row.person_id == person_id
    assert row.status == "draft"
    assert row.message_text is None
    assert row.theme_id is None


def test_set_message_persists_text_and_source_turns(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            set_message_sync(
                cur,
                tribute_id=tribute_id,
                message_text="I never said it, but thank you.",
                source_turns=[{"role": "user", "text": "raw words"}],
            )
            conn.commit()
            row = fetch_tribute_sync(cur, tribute_id=tribute_id)

    assert row is not None
    assert row.message_text == "I never said it, but thank you."


def test_set_status_advances_lifecycle(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            set_status_sync(cur, tribute_id=tribute_id, status="ready")
            conn.commit()
            row = fetch_tribute_sync(cur, tribute_id=tribute_id)

    assert row is not None
    assert row.status == "ready"


def test_fetch_missing_tribute_returns_none(db_pool) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            row = fetch_tribute_sync(
                cur, tribute_id="00000000-0000-0000-0000-000000000000"
            )
    assert row is None
```

- [ ] **Step 4: Write the progress DB test**

Create `tests/tribute/test_progress.py`:

```python
"""DB tests for the tribute_status view via fetch_tribute_progress_sync.

Walks a tribute from 0% (empty graph) to 100%/ready by filling each
checklist slot, asserting the view's weighted percent at each step.
"""

from __future__ import annotations

import json

from flashback.tribute.progress import fetch_tribute_progress_sync
from flashback.tribute.repository import insert_tribute_sync, set_message_sync


def _slot(progress, key: str):
    return next(s for s in progress.slots if s.key == key)


def _add_qualifying_moment(cur, person_id: str, title: str) -> None:
    # sensory_details non-empty => qualifying.
    cur.execute(
        """
        INSERT INTO moments (person_id, title, narrative, sensory_details)
        VALUES (%s, %s, %s, %s)
        """,
        (person_id, title, "a narrative", "the smell of diesel and rain"),
    )


def _set_appearance_ground_truth(cur, person_id: str) -> None:
    gt = {
        "region": {"value": "South India", "provenance": "tap",
                   "confidence": "high", "updated_at": "2026-06-14T00:00:00Z"},
        "birth_era": {"value": "1950s", "provenance": "onboarding",
                      "confidence": "medium", "updated_at": "2026-06-14T00:00:00Z"},
        "attire": {"value": "white cotton shirt", "provenance": "inferred",
                   "confidence": "medium", "updated_at": "2026-06-14T00:00:00Z"},
    }
    cur.execute(
        "UPDATE persons SET ground_truth = %s WHERE id = %s",
        (json.dumps(gt), person_id),
    )


def _add_trait(cur, person_id: str) -> None:
    cur.execute(
        "INSERT INTO traits (person_id, name, description, status) "
        "VALUES (%s, 'patient', NULL, 'active')",
        (person_id,),
    )


def test_progress_empty_tribute_is_zero(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert progress.percent == 0
    assert progress.ready is False
    assert all(s.filled is False for s in progress.slots)


def test_progress_fills_each_slot_to_ready(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)

            # memories: 3 qualifying moments => +40
            for i in range(3):
                _add_qualifying_moment(cur, person_id, f"Memory {i}")
            # appearance: ground_truth => +20
            _set_appearance_ground_truth(cur, person_id)
            # signature: one active trait => +10
            _add_trait(cur, person_id)
            # message: => +30
            set_message_sync(
                cur, tribute_id=tribute_id, message_text="Thank you, Dad."
            )
            conn.commit()

            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert _slot(progress, "memories").filled is True
    assert _slot(progress, "appearance").filled is True
    assert _slot(progress, "signature").filled is True
    assert _slot(progress, "message").filled is True
    assert progress.percent == 100
    assert progress.ready is True


def test_progress_partial_memories_scale_weight(db_pool, make_person) -> None:
    # 2 of 3 memories => 2/3 * 40 ~= 27, no other slots => not ready.
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            _add_qualifying_moment(cur, person_id, "Memory A")
            _add_qualifying_moment(cur, person_id, "Memory B")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert _slot(progress, "memories").filled is False  # needs 3
    assert progress.percent == 27  # floor(2/3 * 40)
    assert progress.ready is False
```

- [ ] **Step 5: Write the checklist unit test**

Create `tests/tribute/test_checklist.py`:

```python
"""Pure unit checks on the tribute checklist config."""

from __future__ import annotations

from flashback.tribute.checklist import SLOT_KEYS, SLOTS


def test_slot_keys_are_the_four_expected() -> None:
    assert SLOT_KEYS == ("memories", "message", "appearance", "signature")


def test_weights_sum_to_100() -> None:
    assert sum(s.weight for s in SLOTS) == 100


def test_slots_ordered_by_descending_weight() -> None:
    weights = [s.weight for s in SLOTS]
    assert weights == sorted(weights, reverse=True)


def test_every_slot_has_label_and_hint() -> None:
    for s in SLOTS:
        assert s.label.strip()
        assert s.hint.strip()
```

- [ ] **Step 6: Run the full suite**

Ensure the test DB containers are running first (`docker start` them if needed — see the project test setup), then:

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:15432/flashback_test pytest tests/tribute -v`
Expected: all tests in `tests/tribute` PASS.

Then run the whole suite to confirm no regression from the migration:

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:15432/flashback_test pytest -q`
Expected: PASS, modulo the known pre-existing failures recorded in the test-environment notes.

> Note: on Windows PowerShell, set the env var with `$env:TEST_DATABASE_URL = "..."` before the `pytest` call rather than the inline `VAR=value` prefix. Confirm the actual `TEST_DATABASE_URL` value from the project's test config — the URL above is illustrative.

- [ ] **Step 7: Commit**

```bash
git add tests/tribute/
git commit -m "test(tribute): data foundation — migration, repository, progress view

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

## Plan Self-Review

**Spec coverage (Plan 1 slice):**
- `tributes` table (spec §4) → Task 1 ✓
- `tribute` theme kind (spec §4) → Task 1 ✓
- `tribute_status` view computing slots + percent, read by Node (spec §7) → Task 1 ✓
- Checklist weights memories 40 / message 30 / appearance 20 / signature 10 (spec §4) → Task 1 (view) + Task 3 (config) ✓
- Slot probes read from existing graph; only `message_text` is new state (spec §4) → Task 1 view JOINs ✓
- Repository for the new row → Task 2 ✓
- Progress reader returning slots (spec §7) → Task 3 ✓
- Deferred to later plans (correctly absent here): `message_answer` sidecar (§5, Plan 2), assembly + compiled jobs + `/generate` (§8, Plan 3), Father's Day skin + live `/turn` meter (§9, Plan 4), async repository surfaces (Plan 3).

**Placeholder scan:** No TBD/TODO. The one illustrative item (the `TEST_DATABASE_URL` value in Task 4 Step 6) is explicitly flagged as needing confirmation against the project test config — not a code placeholder.

**Type consistency:** `insert_tribute_sync`/`fetch_tribute_sync`/`set_message_sync`/`set_status_sync` names match between `repository.py` (Task 2) and `test_repository.py` (Task 4). `fetch_tribute_progress_sync`, `TributeProgress`, `TributeSlot` match between `progress.py` (Task 3) and `test_progress.py` (Task 4). View column names (`memories_count`, `message_present`, `appearance_present`, `signature_present`, `percent`, `ready`) match between the view SQL (Task 1) and `_PROGRESS_SQL` (Task 3). `SLOTS`/`SLOT_KEYS` match between `checklist.py` (Task 3) and `test_checklist.py` (Task 4).
