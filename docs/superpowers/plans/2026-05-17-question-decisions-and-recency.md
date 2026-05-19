# Question Decisions + Recency Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture explicit user decisions on producer-bank questions (Skip / Don't ask again / I'll tell you later) and fix the surfacing bug where old high-priority questions crowd out fresh ones, so the agent stops re-asking questions a contributor has signaled they don't want to answer.

**Architecture:** Add a `question_decisions` table to store cross-session ground truth from explicit user taps; wire it into the steady selector's eligibility query and rank scorer. Add a recency term to `combined_score` so age decays a question's standing. Add a session-scoped same-source cooldown via Working Memory. Surface chips in `/turn` and `/session/start` response metadata; accept decisions back via a new optional `question_decision` field on the next `/turn`.

**Tech Stack:** Python 3.x, Postgres (psycopg async), Valkey, FastAPI, Pydantic v2. Existing migration runner under `migrations/`. Tests in `pytest`.

---

## Scope notes

**Chips render on:**
- Every producer-bank question (sources: `dropped_reference`, `underdeveloped_entity`, `thread_deepen`, `life_period_gap`, `universal_dimension`) returned via `/turn`, in both starter and steady phase.
- The session-start opener question in starter phase (requires wiring `select_question` into `handle_session_start`).

**Chips do NOT render on:**
- Coverage taps (P0 — `source='coverage_tap'`). The existing `select_coverage_tap` tap-card surface in starter phase keeps its current UI: question text + 4 LLM-generated answer-option chips + free text + skip. Unchanged.
- Theme-unlock archetype questions (mid-chat in switch intent — kept untouched per design).
- Onboarding archetype answers (kept untouched per design).

**Tap-card surface is now P0-only.** Previously, `promote_seeded_to_tap` ran in starter phase and promoted producer-bank seeded questions (P2/P3/P5) into tap cards alongside the coverage-tap surface. With the chip surface in place, that promotion path becomes a no-op for producer-bank sources — those questions are inlined into the bot reply and the new chip row sits beneath. The tap-card UI continues to fire only for genuine coverage gaps (P0). This avoids two skip buttons (the tap-card's existing one and the new chip row's one) competing for the same question.

**Cooldown semantics:**
| Action | Edge in DB | Re-eligibility |
|---|---|---|
| `skip` | row in `question_decisions` | 3-step fallback: excluded if any non-skipped candidate exists, otherwise eligible |
| `suppress` | row in `question_decisions` | Permanent — only re-eligible via explicit `POST /question_decisions/clear` admin path (out of scope here) |
| `defer` | row in `question_decisions` with `decided_at` | Excluded for the rest of the current session; in the next session, eligible with a `+1.0` defer-boost added to `combined_score` |

---

## File Structure

**New files:**
- `migrations/0021_question_decisions.up.sql` / `.down.sql` — schema for the new table.
- `src/flashback/question_decisions/__init__.py` — package marker.
- `src/flashback/question_decisions/repository.py` — CRUD over `question_decisions`.
- `src/flashback/question_decisions/schema.py` — Pydantic models + `Action` literal.
- `tests/question_decisions/__init__.py`
- `tests/question_decisions/test_repository.py`
- `tests/phase_gate/test_recency_ranking.py`

**Modified files:**
- `src/flashback/phase_gate/queries.py` — add `NOT EXISTS` clauses for suppress / skip cooldown to `SELECT_STEADY_CANDIDATES` and `SELECT_UNANSWERED_COVERAGE_TAP`. Return `created_at` for recency math (already present on STEADY query; add to coverage tap).
- `src/flashback/phase_gate/ranking.py` — add `RECENCY_WEIGHT`, `DEFER_BOOST`, `recency_score`, `defer_boost_score`; extend `combined_score` signature.
- `src/flashback/phase_gate/steady_selector.py` — thread `now`, deferred ids, last source through `combined_score`; pass through to the SQL via new params; 3-step fallback for skip exhaustion.
- `src/flashback/working_memory/schema.py` — add `last_seeded_source: str` field.
- `src/flashback/working_memory/client.py` — extend `set_seeded_question` to take `source: str | None` and persist it.
- `src/flashback/orchestrator/steps/append_response.py` — pass `state.selection.source` to `set_seeded_question`.
- `src/flashback/orchestrator/steps/starter_opener.py` — same; plus new `select_starter_question` step that runs before `generate_opener` for starter-phase persons and threads `anchor_question_text` into `StarterContext`.
- `src/flashback/orchestrator/steps/promote_seeded_to_tap.py` — make the promotion a no-op when `state.selection.source` is in `PRODUCER_SOURCES`. Tap promotion now only applies to genuine coverage gaps surfaced by `select_coverage_tap`.
- `src/flashback/orchestrator/orchestrator.py` — insert `select_starter_question` into `handle_session_start` pipeline before `generate_opener`.
- `src/flashback/orchestrator/steps/select_question.py` — accept `wm_state.last_seeded_source` and pass through to the selector.
- `src/flashback/orchestrator/protocol.py` — add `QuestionChips` dataclass; add `chips: QuestionChips | None` to `SessionStartResult` and `TurnResult`.
- `src/flashback/http/models.py` — add `QuestionDecisionInput` and `QuestionChipsOut`; add `question_decision` to `TurnRequest`; add `question_chips` to `TurnMetadata` and `SessionStartMetadata`.
- `src/flashback/http/routes/turn.py` — persist incoming `question_decision` before pipeline.
- `src/flashback/http/routes/session.py` — surface `question_chips` in response.
- `local/server.py` + `local/static/index.html` + `local/static/app.js` — render chips and POST decisions on click.

---

## Task 1: Migration for `question_decisions` table

**Files:**
- Create: `migrations/0021_question_decisions.up.sql`
- Create: `migrations/0021_question_decisions.down.sql`

- [ ] **Step 1: Write the up migration**

`migrations/0021_question_decisions.up.sql`:

```sql
-- Captures explicit user decisions on producer-bank questions.
-- One active row per (question_id, person_id); supersession via status flip.
-- Read path: phase_gate eligibility queries do NOT EXISTS against this table.

CREATE TABLE question_decisions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id uuid NOT NULL REFERENCES questions(id),
    person_id   uuid NOT NULL REFERENCES persons(id),
    action      text NOT NULL CHECK (action IN ('skip', 'suppress', 'defer')),
    decided_at  timestamptz NOT NULL DEFAULT now(),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    superseded_by uuid REFERENCES question_decisions(id)
);

CREATE UNIQUE INDEX idx_question_decisions_active
    ON question_decisions (question_id, person_id)
    WHERE status = 'active';

CREATE INDEX idx_question_decisions_lookup
    ON question_decisions (person_id, action, decided_at)
    WHERE status = 'active';

CREATE VIEW active_question_decisions AS
    SELECT *
    FROM question_decisions
    WHERE status = 'active';

COMMENT ON TABLE question_decisions IS
    'Explicit user decisions on producer-bank questions. See CLAUDE.md and the plan at docs/superpowers/plans/2026-05-17-question-decisions-and-recency.md.';
```

- [ ] **Step 2: Write the down migration**

`migrations/0021_question_decisions.down.sql`:

```sql
DROP VIEW IF EXISTS active_question_decisions;
DROP INDEX IF EXISTS idx_question_decisions_lookup;
DROP INDEX IF EXISTS idx_question_decisions_active;
DROP TABLE IF EXISTS question_decisions;
```

- [ ] **Step 3: Apply migration locally**

Run: `python -m flashback.db.migrate up`
Expected: `0021_question_decisions` applied. No errors.

- [ ] **Step 4: Verify tables**

Run:
```bash
psql -c "\d question_decisions"
psql -c "\dv active_question_decisions"
```
Expected: table and view both present.

- [ ] **Step 5: Commit**

```bash
git add migrations/0021_question_decisions.up.sql migrations/0021_question_decisions.down.sql
git commit -m "feat(schema): question_decisions table for explicit skip/suppress/defer"
```

---

## Task 2: Decision schema + repository

**Files:**
- Create: `src/flashback/question_decisions/__init__.py`
- Create: `src/flashback/question_decisions/schema.py`
- Create: `src/flashback/question_decisions/repository.py`
- Create: `tests/question_decisions/__init__.py`
- Create: `tests/question_decisions/test_repository.py`

- [ ] **Step 1: Write schema module**

`src/flashback/question_decisions/schema.py`:

```python
"""Pydantic models + literal action set for question_decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Action = Literal["skip", "suppress", "defer"]
ACTIONS: tuple[Action, ...] = ("skip", "suppress", "defer")


class QuestionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    question_id: UUID
    person_id: UUID
    action: Action
    decided_at: datetime
    status: Literal["active", "superseded"]
```

- [ ] **Step 2: Write empty `__init__.py` files**

`src/flashback/question_decisions/__init__.py`:

```python
from flashback.question_decisions.schema import (
    ACTIONS,
    Action,
    QuestionDecision,
)

__all__ = ["ACTIONS", "Action", "QuestionDecision"]
```

`tests/question_decisions/__init__.py`: empty file.

- [ ] **Step 3: Write failing repository tests**

`tests/question_decisions/test_repository.py`:

```python
"""Repository CRUD tests for question_decisions."""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.question_decisions.repository import (
    QuestionDecisionRepository,
)


@pytest.mark.asyncio
async def test_record_decision_inserts_active_row(db_pool, seeded_question, person):
    repo = QuestionDecisionRepository(db_pool)
    decision = await repo.record(
        person_id=person.id,
        question_id=seeded_question.id,
        action="skip",
    )
    assert decision.action == "skip"
    assert decision.status == "active"


@pytest.mark.asyncio
async def test_record_supersedes_prior_active(db_pool, seeded_question, person):
    repo = QuestionDecisionRepository(db_pool)
    first = await repo.record(person.id, seeded_question.id, "skip")
    second = await repo.record(person.id, seeded_question.id, "defer")
    actives = await repo.list_active(person.id)
    assert len(actives) == 1
    assert actives[0].id == second.id
    assert actives[0].action == "defer"
    # Prior row is superseded
    history = await repo.list_history(person.id, seeded_question.id)
    superseded = [d for d in history if d.id == first.id][0]
    assert superseded.status == "superseded"


@pytest.mark.asyncio
async def test_list_active_returns_only_active(db_pool, seeded_question, person):
    repo = QuestionDecisionRepository(db_pool)
    await repo.record(person.id, seeded_question.id, "skip")
    await repo.record(person.id, seeded_question.id, "suppress")
    actives = await repo.list_active(person.id)
    assert len(actives) == 1
    assert actives[0].action == "suppress"
```

Run: `pytest tests/question_decisions/test_repository.py -v`
Expected: ImportError on `QuestionDecisionRepository`.

- [ ] **Step 4: Implement repository**

`src/flashback/question_decisions/repository.py`:

```python
"""Async repository over the question_decisions table."""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.question_decisions.schema import Action, QuestionDecision

_INSERT_AND_SUPERSEDE = """
WITH supersede AS (
    UPDATE question_decisions
       SET status = 'superseded',
           superseded_by = %(new_id)s
     WHERE person_id   = %(person_id)s
       AND question_id = %(question_id)s
       AND status      = 'active'
    RETURNING id
)
INSERT INTO question_decisions (id, question_id, person_id, action)
VALUES (%(new_id)s, %(question_id)s, %(person_id)s, %(action)s)
RETURNING id, question_id, person_id, action, decided_at, status
"""

_LIST_ACTIVE = """
SELECT id, question_id, person_id, action, decided_at, status
  FROM active_question_decisions
 WHERE person_id = %(person_id)s
 ORDER BY decided_at DESC
"""

_LIST_HISTORY = """
SELECT id, question_id, person_id, action, decided_at, status
  FROM question_decisions
 WHERE person_id   = %(person_id)s
   AND question_id = %(question_id)s
 ORDER BY created_at DESC
"""


class QuestionDecisionRepository:
    def __init__(self, db_pool: AsyncConnectionPool) -> None:
        self._pool = db_pool

    async def record(
        self,
        person_id: UUID,
        question_id: UUID,
        action: Action,
    ) -> QuestionDecision:
        from uuid import uuid4

        new_id = uuid4()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_AND_SUPERSEDE,
                    {
                        "new_id": new_id,
                        "person_id": person_id,
                        "question_id": question_id,
                        "action": action,
                    },
                )
                row = await cur.fetchone()
        return _row_to_model(row)

    async def list_active(self, person_id: UUID) -> list[QuestionDecision]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_LIST_ACTIVE, {"person_id": person_id})
                rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]

    async def list_history(
        self, person_id: UUID, question_id: UUID
    ) -> list[QuestionDecision]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    _LIST_HISTORY,
                    {"person_id": person_id, "question_id": question_id},
                )
                rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]


def _row_to_model(row) -> QuestionDecision:
    return QuestionDecision(
        id=row[0],
        question_id=row[1],
        person_id=row[2],
        action=row[3],
        decided_at=row[4],
        status=row[5],
    )
```

- [ ] **Step 5: Run tests; expect pass**

Run: `pytest tests/question_decisions/test_repository.py -v`
Expected: all three pass. If fixtures `seeded_question` and `person` are missing, add them to `tests/conftest.py` following the pattern of existing fixtures there.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/question_decisions tests/question_decisions
git commit -m "feat(question_decisions): repository + Pydantic schema"
```

---

## Task 3: Eligibility query updates — exclude suppress and skip-with-fallback

**Files:**
- Modify: `src/flashback/phase_gate/queries.py`
- Modify: `tests/phase_gate/test_queries.py`

- [ ] **Step 1: Add failing query test**

Add to `tests/phase_gate/test_queries.py`:

```python
@pytest.mark.asyncio
async def test_steady_candidates_excludes_suppressed_questions(
    db_pool, person, producer_question_factory, decision_repo
):
    q1 = await producer_question_factory(person.id, source="universal_dimension")
    q2 = await producer_question_factory(person.id, source="universal_dimension")
    await decision_repo.record(person.id, q1.id, "suppress")

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                SELECT_STEADY_CANDIDATES,
                {
                    "person_id": person.id,
                    "recent_ids": [],
                    "sources": ["universal_dimension"],
                    "exclude_skipped": True,
                },
            )
            rows = await cur.fetchall()
    ids = {row[0] for row in rows}
    assert q1.id not in ids
    assert q2.id in ids


@pytest.mark.asyncio
async def test_steady_candidates_skipped_falls_back_when_no_alternative(
    db_pool, person, producer_question_factory, decision_repo
):
    q1 = await producer_question_factory(person.id, source="universal_dimension")
    await decision_repo.record(person.id, q1.id, "skip")

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            # First call with exclude_skipped=True excludes q1
            await cur.execute(
                SELECT_STEADY_CANDIDATES,
                {
                    "person_id": person.id,
                    "recent_ids": [],
                    "sources": ["universal_dimension"],
                    "exclude_skipped": True,
                },
            )
            rows_strict = await cur.fetchall()
            # Second call with exclude_skipped=False allows q1 back
            await cur.execute(
                SELECT_STEADY_CANDIDATES,
                {
                    "person_id": person.id,
                    "recent_ids": [],
                    "sources": ["universal_dimension"],
                    "exclude_skipped": False,
                },
            )
            rows_loose = await cur.fetchall()
    assert all(row[0] != q1.id for row in rows_strict)
    assert any(row[0] == q1.id for row in rows_loose)
```

Run: `pytest tests/phase_gate/test_queries.py::test_steady_candidates_excludes_suppressed_questions -v`
Expected: FAIL (`exclude_skipped` parameter not recognised by SQL).

- [ ] **Step 2: Modify queries.py**

Replace `SELECT_STEADY_CANDIDATES` in `src/flashback/phase_gate/queries.py`:

```python
SELECT_STEADY_CANDIDATES = """
SELECT q.id, q.text, q.source, q.attributes, q.created_at,
       d.action AS decision_action,
       d.decided_at AS decision_decided_at
FROM active_questions q
LEFT JOIN active_question_decisions d
  ON d.question_id = q.id
 AND d.person_id   = %(person_id)s
WHERE q.person_id = %(person_id)s
  AND q.source    = ANY(%(sources)s::text[])
  AND NOT (q.id   = ANY(%(recent_ids)s::uuid[]))
  AND (d.action IS NULL OR d.action != 'suppress')
  AND (
        NOT %(exclude_skipped)s
        OR d.action IS NULL
        OR d.action != 'skip'
      )
ORDER BY
  CASE q.source
    WHEN 'dropped_reference' THEN 0
    WHEN 'underdeveloped_entity' THEN 1
    WHEN 'thread_deepen' THEN 2
    WHEN 'life_period_gap' THEN 3
    WHEN 'universal_dimension' THEN 4
    ELSE 5
  END,
  q.created_at DESC
LIMIT 50
"""
```

Replace `SELECT_UNANSWERED_COVERAGE_TAP` so it also excludes suppressed taps for this person (coverage taps are global; the join lives on `person_id` so a row only excludes for the relevant person):

```python
SELECT_UNANSWERED_COVERAGE_TAP = """
SELECT q.id, q.text
FROM active_questions q
WHERE q.source = 'coverage_tap'
  AND q.person_id IS NULL
  AND q.attributes->>'dimension' = %(dimension)s
  AND NOT (q.id = ANY(%(recent_ids)s::uuid[]))
  AND NOT EXISTS (
    SELECT 1
    FROM active_question_decisions d
    WHERE d.question_id = q.id
      AND d.person_id   = %(person_id)s
      AND d.action      = 'suppress'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM active_edges e
    JOIN active_moments m ON m.id = e.to_id
    WHERE e.from_kind = 'question'
      AND e.from_id = q.id
      AND e.edge_type = 'answered_by'
      AND e.to_kind = 'moment'
      AND m.person_id = %(person_id)s
  )
ORDER BY random()
LIMIT 1
"""
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/phase_gate/test_queries.py -v`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/flashback/phase_gate/queries.py tests/phase_gate/test_queries.py
git commit -m "feat(phase_gate): exclude suppressed + skip-with-fallback in candidates"
```

---

## Task 4: Recency + defer-boost in `combined_score`

**Files:**
- Modify: `src/flashback/phase_gate/ranking.py`
- Create: `tests/phase_gate/test_recency_ranking.py`

- [ ] **Step 1: Write failing ranking tests**

`tests/phase_gate/test_recency_ranking.py`:

```python
"""Recency decay + defer-boost in combined_score."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flashback.phase_gate.ranking import (
    DEFER_BOOST,
    RECENCY_WEIGHT,
    combined_score,
    recency_score,
)


NOW = datetime(2026, 5, 17, tzinfo=timezone.utc)


def test_recency_score_fresh_is_one():
    assert recency_score(NOW, NOW) == 1.0


def test_recency_score_decays():
    age_30d_score = recency_score(NOW - timedelta(days=30), NOW)
    age_90d_score = recency_score(NOW - timedelta(days=90), NOW)
    assert 0.30 < age_30d_score < 0.45
    assert age_90d_score < 0.10


def test_fresh_lower_tier_beats_old_higher_tier_when_age_delta_large():
    # underdeveloped_entity (priority 3) created 90 days ago
    # vs life_period_gap (priority 1) created today
    old_high = combined_score(
        source="underdeveloped_entity",
        question_themes=set(),
        recent_themes=set(),
        active_theme_slug=None,
        created_at=NOW - timedelta(days=90),
        now=NOW,
        is_deferred=False,
    )
    fresh_low = combined_score(
        source="life_period_gap",
        question_themes=set(),
        recent_themes=set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
        is_deferred=False,
    )
    assert fresh_low > old_high


def test_defer_boost_makes_deferred_outrank_same_tier_fresh():
    deferred = combined_score(
        source="universal_dimension",
        question_themes=set(),
        recent_themes=set(),
        active_theme_slug=None,
        created_at=NOW - timedelta(days=30),
        now=NOW,
        is_deferred=True,
    )
    fresh = combined_score(
        source="universal_dimension",
        question_themes=set(),
        recent_themes=set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
        is_deferred=False,
    )
    assert deferred > fresh


def test_source_priority_still_dominant_when_age_equal():
    fresh_high = combined_score(
        source="dropped_reference",
        question_themes=set(),
        recent_themes=set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
        is_deferred=False,
    )
    fresh_low = combined_score(
        source="universal_dimension",
        question_themes=set(),
        recent_themes=set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
        is_deferred=False,
    )
    assert fresh_high > fresh_low
```

Run: `pytest tests/phase_gate/test_recency_ranking.py -v`
Expected: FAIL — `RECENCY_WEIGHT`, `recency_score`, and the new `combined_score` signature don't exist yet.

- [ ] **Step 2: Update ranking.py**

Replace `src/flashback/phase_gate/ranking.py`:

```python
"""Deterministic ranking helpers for Phase Gate question selection."""

from __future__ import annotations

import math
from datetime import datetime

DIVERSITY_WEIGHT: float = 2.0
RECENCY_WEIGHT: float = 2.0
DEFER_BOOST: float = 1.0
RECENTLY_ASKED_WINDOW: int = 5
RECENCY_HALF_LIFE_DAYS: float = 30.0

SOURCE_PRIORITY: tuple[str, ...] = (
    "dropped_reference",
    "underdeveloped_entity",
    "thread_deepen",
    "life_period_gap",
    "universal_dimension",
)
TIEBREAKER_DIMENSIONS: tuple[str, ...] = (
    "era",
    "relation",
    "place",
    "voice",
    "sensory",
)

THEME_BIAS_WEIGHT: float = 1.5


def source_priority_score(source: str) -> float:
    """Higher is better. ``dropped_reference`` = 4.0; unknowns = 0.0."""
    try:
        rank = SOURCE_PRIORITY.index(source)
    except ValueError:
        return 0.0
    return float(len(SOURCE_PRIORITY) - 1 - rank)


def diversity_score(question_themes: set[str], recent_themes: set[str]) -> float:
    if not question_themes:
        return 0.0
    overlap = len(question_themes & recent_themes)
    return 1.0 - (overlap / len(question_themes))


def theme_bias_score(
    question_themes: set[str], active_theme_slug: str | None
) -> float:
    if not active_theme_slug:
        return 0.0
    if not question_themes:
        return 0.0
    return 1.0 if active_theme_slug in question_themes else 0.0


def recency_score(created_at: datetime, now: datetime) -> float:
    """Exponential decay with a 30-day half-life. Fresh = 1.0."""
    delta = now - created_at
    days = max(0.0, delta.total_seconds() / 86400.0)
    return math.exp(-days / RECENCY_HALF_LIFE_DAYS)


def combined_score(
    source: str,
    question_themes: set[str],
    recent_themes: set[str],
    *,
    active_theme_slug: str | None = None,
    created_at: datetime,
    now: datetime,
    is_deferred: bool = False,
) -> float:
    return (
        source_priority_score(source)
        + DIVERSITY_WEIGHT * diversity_score(question_themes, recent_themes)
        + THEME_BIAS_WEIGHT * theme_bias_score(question_themes, active_theme_slug)
        + RECENCY_WEIGHT * recency_score(created_at, now)
        + (DEFER_BOOST if is_deferred else 0.0)
    )
```

- [ ] **Step 3: Run ranking tests**

Run: `pytest tests/phase_gate/test_recency_ranking.py -v`
Expected: pass.

- [ ] **Step 4: Update existing combined_score callers**

The existing test suite calls `combined_score(source, question_themes, recent_themes, active_theme_slug=...)`. Find all callers:

Run: `grep -rn "combined_score" src/ tests/`

For each test caller that doesn't pass `created_at` / `now`, update the call. The production caller `steady_selector.py` will be updated in Task 5 — leave it for now but expect tests there to fail temporarily.

- [ ] **Step 5: Run full ranking + phase_gate tests**

Run: `pytest tests/phase_gate -v`
Expected: pass on ranking tests; `test_steady_selector.py` may fail — that's fixed in Task 5.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/phase_gate/ranking.py tests/phase_gate/test_recency_ranking.py tests/phase_gate
git commit -m "feat(phase_gate): recency decay + defer-boost in combined_score"
```

---

## Task 5: Steady selector — thread `now`, deferred set, last source

**Files:**
- Modify: `src/flashback/phase_gate/steady_selector.py`
- Modify: `tests/phase_gate/test_steady_selector.py`

- [ ] **Step 1: Update test for last-source cooldown**

Add to `tests/phase_gate/test_steady_selector.py`:

```python
@pytest.mark.asyncio
async def test_selector_avoids_same_source_when_alternative_exists(
    db_pool, wm, person, producer_question_factory
):
    older_ud = await producer_question_factory(
        person.id, source="underdeveloped_entity"
    )
    fresh_lpg = await producer_question_factory(
        person.id, source="life_period_gap"
    )
    selector = SteadySelector(db_pool, wm)
    result = await selector.select(
        person_id=person.id,
        session_id=uuid4(),
        last_seeded_source="underdeveloped_entity",
    )
    assert result.question_id == fresh_lpg.id


@pytest.mark.asyncio
async def test_selector_falls_back_to_skipped_when_no_alternative(
    db_pool, wm, person, producer_question_factory, decision_repo
):
    only_q = await producer_question_factory(
        person.id, source="universal_dimension"
    )
    await decision_repo.record(person.id, only_q.id, "skip")
    selector = SteadySelector(db_pool, wm)
    result = await selector.select(
        person_id=person.id,
        session_id=uuid4(),
    )
    assert result.question_id == only_q.id
    assert "fallback" in result.rationale.lower()
```

Run: `pytest tests/phase_gate/test_steady_selector.py -v`
Expected: FAIL — `last_seeded_source` not accepted; fallback rationale missing.

- [ ] **Step 2: Update `steady_selector.py`**

Replace `SteadySelector.select` and `_fetch_candidates` in `src/flashback/phase_gate/steady_selector.py`:

```python
from datetime import datetime, timezone

PRODUCER_SOURCES: tuple[str, ...] = (
    "dropped_reference",
    "underdeveloped_entity",
    "thread_deepen",
    "life_period_gap",
    "universal_dimension",
)


class SteadySelector:
    def __init__(self, db_pool: AsyncConnectionPool, working_memory: WorkingMemory):
        self._pool = db_pool
        self._wm = working_memory

    async def select(
        self,
        person_id: UUID,
        session_id: UUID,
        *,
        sources: tuple[str, ...] = STEADY_SOURCES,
        active_theme_slug: str | None = None,
        last_seeded_source: str | None = None,
    ) -> SelectionResult:
        recent_ids = [
            UUID(question_id)
            for question_id in await self._wm.get_recently_asked_question_ids(
                str(session_id)
            )
        ]
        effective_sources = tuple(
            s for s in sources if s != last_seeded_source
        ) or sources  # fall back if cooldown would empty the pool
        recent_themes = await self._fetch_recent_themes(recent_ids)

        candidates = await self._fetch_candidates(
            person_id, recent_ids, effective_sources, exclude_skipped=True
        )
        used_fallback = False
        if not candidates and effective_sources != sources:
            candidates = await self._fetch_candidates(
                person_id, recent_ids, sources, exclude_skipped=True
            )
        if not candidates:
            candidates = await self._fetch_candidates(
                person_id, recent_ids, sources, exclude_skipped=False
            )
            used_fallback = bool(candidates)
        if not candidates:
            return SelectionResult(
                phase="steady",
                rationale="steady bank empty; no seeded question",
            )

        now = datetime.now(timezone.utc)
        scored = [
            _ScoredCandidate(
                candidate=candidate,
                score=combined_score(
                    candidate.source,
                    candidate.themes,
                    recent_themes,
                    active_theme_slug=active_theme_slug,
                    created_at=candidate.created_at,
                    now=now,
                    is_deferred=candidate.decision_action == "defer",
                ),
            )
            for candidate in candidates
        ]
        scored.sort(key=lambda item: (item.score, item.candidate.created_at), reverse=True)
        selected = _apply_universal_dimension_demotion(scored)
        candidate = selected.candidate
        rationale_suffix = " (fallback to skipped)" if used_fallback else ""
        return SelectionResult(
            phase="steady",
            question_id=candidate.id,
            question_text=candidate.text,
            source=candidate.source,
            dimension=None,
            rationale=(
                f"steady selected {candidate.source}; "
                f"score={selected.score:.3f}; recent_themes={len(recent_themes)}"
                f"{rationale_suffix}"
            ),
        )

    async def _fetch_candidates(
        self,
        person_id: UUID,
        recent_ids: list[UUID],
        sources: tuple[str, ...],
        *,
        exclude_skipped: bool,
    ) -> list["_Candidate"]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    SELECT_STEADY_CANDIDATES,
                    {
                        "person_id": person_id,
                        "recent_ids": recent_ids,
                        "sources": list(sources),
                        "exclude_skipped": exclude_skipped,
                    },
                )
                rows = await cur.fetchall()
        return [
            _Candidate(
                id=row[0],
                text=row[1],
                source=row[2],
                attributes=row[3] if isinstance(row[3], dict) else {},
                created_at=row[4],
                decision_action=row[5],
                decision_decided_at=row[6],
            )
            for row in rows
        ]
```

Update `_Candidate`:

```python
@dataclass(frozen=True)
class _Candidate:
    id: UUID
    text: str
    source: str
    attributes: dict[str, Any]
    created_at: datetime
    decision_action: str | None = None
    decision_decided_at: datetime | None = None

    @property
    def themes(self) -> set[str]:
        raw = self.attributes.get("themes", [])
        if not isinstance(raw, list):
            return set()
        return {str(theme) for theme in raw if theme}
```

- [ ] **Step 3: Update `gate.py` to thread `last_seeded_source`**

In `src/flashback/phase_gate/gate.py::PhaseGate.select_next_question`, accept and pass through:

```python
async def select_next_question(
    self,
    person_id: UUID,
    session_id: UUID,
    recently_asked_ids: list[UUID] | None = None,
    active_theme_slug: str | None = None,
    last_seeded_source: str | None = None,
) -> SelectionResult:
    phase = await self._read_phase(person_id)
    if phase == "starter":
        result = await self._steady.select(
            person_id,
            session_id,
            sources=STARTER_FALLBACK_SOURCES,
            active_theme_slug=active_theme_slug,
            last_seeded_source=last_seeded_source,
        )
    else:
        result = await self._steady.select(
            person_id,
            session_id,
            active_theme_slug=active_theme_slug,
            last_seeded_source=last_seeded_source,
        )
    result.phase = phase
    result.rationale = result.rationale or f"{phase} selection"
    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/phase_gate -v`
Expected: pass, including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/phase_gate
git commit -m "feat(phase_gate): same-source cooldown + skip fallback in selector"
```

---

## Task 6: Working Memory — track `last_seeded_source`

**Files:**
- Modify: `src/flashback/working_memory/schema.py`
- Modify: `src/flashback/working_memory/client.py`
- Modify: `tests/working_memory/test_keys.py` or equivalent

- [ ] **Step 1: Add failing client test**

Add to `tests/working_memory/test_recently_asked.py`:

```python
@pytest.mark.asyncio
async def test_set_seeded_question_persists_source(wm, session_id):
    await wm.set_seeded_question(session_id, "abc-question-id", source="universal_dimension")
    state = await wm.get_state(session_id)
    assert state.last_seeded_question_id == "abc-question-id"
    assert state.last_seeded_source == "universal_dimension"


@pytest.mark.asyncio
async def test_set_seeded_question_clears_source_on_none(wm, session_id):
    await wm.set_seeded_question(session_id, "abc-question-id", source="universal_dimension")
    await wm.set_seeded_question(session_id, None)
    state = await wm.get_state(session_id)
    assert state.last_seeded_source == ""
```

Run: `pytest tests/working_memory/test_recently_asked.py -v -k seeded`
Expected: FAIL on AttributeError or signature mismatch.

- [ ] **Step 2: Extend schema**

In `src/flashback/working_memory/schema.py`, add to `WorkingMemoryState`:

```python
    last_seeded_source: str = ""
```

Add to `serialise_state_for_init`:

```python
        "last_seeded_source": state.last_seeded_source,
```

- [ ] **Step 3: Update client `set_seeded_question`**

In `src/flashback/working_memory/client.py`:

```python
async def set_seeded_question(
    self,
    session_id: str,
    question_id: str | None,
    *,
    source: str | None = None,
) -> None:
    await self.update_signals(
        session_id,
        last_seeded_question_id=question_id or "",
        last_seeded_source=source or "",
    )
```

Make sure `update_signals` accepts `last_seeded_source` — it should already accept arbitrary fields since they go through HSET. If `update_signals` filters allowed fields, add `last_seeded_source` to the allowlist.

- [ ] **Step 4: Run tests**

Run: `pytest tests/working_memory -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/working_memory tests/working_memory
git commit -m "feat(wm): track last_seeded_source for same-source cooldown"
```

---

## Task 7: Orchestrator wiring — pass source through select_question + append_response + starter_opener

**Files:**
- Modify: `src/flashback/orchestrator/steps/select_question.py`
- Modify: `src/flashback/orchestrator/steps/append_response.py`
- Modify: `src/flashback/orchestrator/steps/starter_opener.py`

- [ ] **Step 1: Update `select_question.py`**

Pass `last_seeded_source` from `wm_state` into `select_next_question`:

```python
async def select_question(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "select_question"):
        if deps.phase_gate is None:
            log.info("phase_gate.skipped", reason="not_configured")
            return
        recently_asked_ids: list[UUID] = []
        if deps.working_memory is not None:
            raw_ids = await deps.working_memory.get_recently_asked_question_ids(
                str(state.session_id)
            )
            recently_asked_ids = [UUID(qid) for qid in raw_ids if qid]
        active_theme_slug: str | None = None
        last_seeded_source: str | None = None
        if state.working_memory_state is not None:
            slug = state.working_memory_state.current_theme_slug
            if slug:
                active_theme_slug = slug
            src = state.working_memory_state.last_seeded_source
            if src:
                last_seeded_source = src
        state.selection = await deps.phase_gate.select_next_question(
            person_id=state.person_id,
            session_id=state.session_id,
            recently_asked_ids=recently_asked_ids,
            active_theme_slug=active_theme_slug,
            last_seeded_source=last_seeded_source,
        )
        log.info(
            "phase_gate.selected",
            phase=state.selection.phase,
            question_id=(
                str(state.selection.question_id)
                if state.selection.question_id is not None
                else None
            ),
            source=state.selection.source,
            last_seeded_source=last_seeded_source,
            rationale=state.selection.rationale,
            recently_asked_n=len(recently_asked_ids),
        )
```

- [ ] **Step 2: Update `append_response.py`**

Where it currently calls `set_seeded_question(...)`, also pass the source:

```python
if state.selection and state.selection.question_id is not None:
    question_id = str(state.selection.question_id)
    await deps.working_memory.set_seeded_question(
        str(state.session_id),
        question_id,
        source=state.selection.source,
    )
    await deps.working_memory.append_asked_question(
        str(state.session_id),
        question_id,
    )
```

- [ ] **Step 3: Update `starter_opener.py` `append_opener`**

Same change in the `append_opener` step:

```python
if state.selection and state.selection.question_id is not None:
    question_id = str(state.selection.question_id)
    await deps.working_memory.set_seeded_question(
        session_id=str(state.session_id),
        question_id=question_id,
        source=state.selection.source,
    )
    await deps.working_memory.append_asked_question(
        session_id=str(state.session_id),
        question_id=question_id,
    )
```

- [ ] **Step 4: Make `promote_seeded_to_tap` a no-op for producer-bank sources**

The current flow promotes any seeded question (including producer-bank P2/P3/P5) into a tap card when running in starter phase with no coverage gap pending. With chips replacing that surface for producer-bank questions, the promotion path must early-return.

Add a failing test first. In `tests/orchestrator/test_promote_seeded_to_tap.py` (create if missing):

```python
@pytest.mark.asyncio
async def test_producer_bank_source_is_not_promoted_to_tap(
    deps, starter_turn_state_with_producer_selection
):
    state = starter_turn_state_with_producer_selection  # selection.source == "universal_dimension"
    await promote_seeded_to_tap(state, deps)
    assert state.taps == []
    # The seeded selection remains intact for chip rendering
    assert state.selection is not None
    assert state.selection.source == "universal_dimension"


@pytest.mark.asyncio
async def test_coverage_tap_path_still_renders_tap(
    deps, starter_turn_state_with_coverage_tap_pending
):
    # Sanity: the existing coverage_tap path is unaffected.
    state = starter_turn_state_with_coverage_tap_pending
    # ... existing assertions ...
```

Run: `pytest tests/orchestrator/test_promote_seeded_to_tap.py -v`
Expected: FAIL — current implementation promotes any producer-bank question into a tap.

In `src/flashback/orchestrator/steps/promote_seeded_to_tap.py`, add the early-return at the top of the function body (after the existing phase-check and coverage-gap-check guards). Use the same `PRODUCER_SOURCES` tuple defined in `phase_gate/steady_selector.py`:

```python
from flashback.phase_gate.steady_selector import PRODUCER_SOURCES

# inside promote_seeded_to_tap:
    if state.selection is None or state.selection.question_id is None:
        return
    if state.selection.source in PRODUCER_SOURCES:
        log.info(
            "seeded_question.tap_promotion_skipped",
            reason="producer_bank_uses_chip_surface",
            source=state.selection.source,
        )
        return
    # ... existing promotion logic continues ...
```

Read the current `promote_seeded_to_tap.py` first to find the right insertion point — preserve all existing guards (starter-phase check, no-coverage-tap-already-emitted check, tap cooldown, etc.) above the new early-return.

Re-run: `pytest tests/orchestrator/test_promote_seeded_to_tap.py -v`
Expected: pass.

- [ ] **Step 5: Run orchestrator tests**

Run: `pytest tests/orchestrator -v`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/orchestrator/steps tests/orchestrator
git commit -m "feat(orchestrator): thread question source + skip tap promotion for producer-bank"
```

---

## Task 8: Session-start seeds a producer-bank question in starter phase

**Files:**
- Modify: `src/flashback/orchestrator/steps/starter_opener.py`
- Modify: `src/flashback/orchestrator/orchestrator.py`
- Modify: `src/flashback/response_generator/__init__.py` (or wherever `StarterContext` lives) — confirm `anchor_question_text` is honored in the opener prompt
- Modify: `tests/orchestrator/test_session_start.py` (or equivalent)

- [ ] **Step 1: Confirm `StarterContext.anchor_question_text` is used by the opener prompt**

Run: `grep -rn "anchor_question_text" src/flashback/response_generator/`

If the prompt template includes it conditionally, no change needed. If not, edit the starter opener system prompt to include `If anchor_question_text is provided, weave it into your opener as a natural conversational question.` See `src/flashback/response_generator/prompts.py` (or equivalent). The prompt change MUST instruct the LLM to use the provided question verbatim or close to verbatim — otherwise the chip metadata will reference a question_id whose text doesn't match what the user heard.

- [ ] **Step 2: Add new step `select_starter_question`**

In `src/flashback/orchestrator/steps/starter_opener.py`:

```python
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.state import SessionStartState


async def select_starter_question(
    state: SessionStartState, deps: OrchestratorDeps
) -> None:
    """Pick a producer-bank question for the starter opener.

    Only runs in starter phase. If the bank is empty (very first session
    post-onboarding), `state.selection` stays None and the opener is
    purely LLM-generated from StarterContext without an anchor question.
    """
    with timed_step(log, "select_starter_question"):
        if state.person_phase != "starter":
            return
        if deps.phase_gate is None:
            return
        # No WM state yet at this point in the pipeline; pass empty recent.
        state.selection = await deps.phase_gate.select_next_question(
            person_id=state.person_id,
            session_id=state.session_id,
            recently_asked_ids=[],
            active_theme_slug=None,
            last_seeded_source=None,
        )
```

- [ ] **Step 3: Wire `anchor_question_text` into `generate_opener`**

Modify `generate_opener` in `starter_opener.py`:

```python
anchor_text = None
if state.selection and state.selection.question_text:
    anchor_text = state.selection.question_text

ctx = StarterContext(
    person_name=state.person_name,
    person_relationship=state.person_relationship,
    person_gender=state.person_gender,
    contributor_display_name=_string_or_none(
        state.session_metadata.get("contributor_display_name")
    ),
    contributor_role=_string_or_none(
        state.session_metadata.get("contributor_role")
        or state.session_metadata.get("role")
    ),
    anchor_question_text=anchor_text,
    anchor_dimension=None,
    prior_session_summary=_string_or_none(
        state.session_metadata.get("prior_session_summary")
    ),
    current_theme_display_name=_string_or_none(
        state.session_metadata.get("current_theme_display_name")
    ),
    current_theme_kind=_string_or_none(
        state.session_metadata.get("current_theme_kind")
    ),
    theme_archetype_answers=[
        a for a in theme_archetype_answers if isinstance(a, dict)
    ],
)
```

- [ ] **Step 4: Insert step into `handle_session_start` pipeline**

In `src/flashback/orchestrator/orchestrator.py`, after `apply_theme_unlock` and before `load_continuity_context`, add:

```python
from flashback.orchestrator.steps.starter_opener import select_starter_question

# ...
await execute(
    policies=SESSION_START_POLICIES,
    step_name="select_starter_question",
    fn=lambda: select_starter_question(state, self._deps),
    state=state,
)
```

- [ ] **Step 5: Add integration test**

In `tests/orchestrator/test_session_start.py`, add:

```python
@pytest.mark.asyncio
async def test_session_start_in_starter_phase_seeds_question(
    orchestrator, starter_person, producer_question_factory
):
    q = await producer_question_factory(
        starter_person.id, source="universal_dimension"
    )
    result = await orchestrator.handle_session_start(
        session_id=uuid4(),
        person_id=starter_person.id,
        role_id=uuid4(),
        session_metadata={},
    )
    assert result.selected_question_id == q.id


@pytest.mark.asyncio
async def test_session_start_in_steady_phase_does_not_seed(
    orchestrator, steady_person, producer_question_factory
):
    await producer_question_factory(
        steady_person.id, source="universal_dimension"
    )
    result = await orchestrator.handle_session_start(
        session_id=uuid4(),
        person_id=steady_person.id,
        role_id=uuid4(),
        session_metadata={},
    )
    assert result.selected_question_id is None
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/orchestrator/test_session_start.py -v`
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/flashback/orchestrator tests/orchestrator
git commit -m "feat(orchestrator): seed producer-bank question on starter-phase /session/start"
```

---

## Task 9: API surface — `QuestionChips` on responses, `question_decision` on `/turn`

**Files:**
- Modify: `src/flashback/orchestrator/protocol.py`
- Modify: `src/flashback/http/models.py`
- Modify: `src/flashback/http/routes/turn.py`
- Modify: `src/flashback/http/routes/session.py`
- Modify: `src/flashback/orchestrator/orchestrator.py`

- [ ] **Step 1: Add `QuestionChips` to protocol**

In `src/flashback/orchestrator/protocol.py`:

```python
class QuestionChips(BaseModel):
    """Chip metadata for a seeded producer-bank question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: UUID
    actions: list[Literal["skip", "suppress", "defer"]] = Field(
        default_factory=lambda: ["skip", "suppress", "defer"]
    )


@dataclass(frozen=True)
class SessionStartResult:
    opener: str
    phase: str
    selected_question_id: UUID | None
    taps: list[Tap]
    chips: QuestionChips | None = None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    intent: str | None
    emotional_temperature: str | None
    segment_boundary: bool
    taps: list[Tap]
    chips: QuestionChips | None = None
```

(Import `Literal` from `typing` if not present.)

- [ ] **Step 2: Populate `chips` in orchestrator results**

In `src/flashback/orchestrator/orchestrator.py`, define a helper at module level:

```python
from flashback.orchestrator.protocol import QuestionChips
from flashback.phase_gate.steady_selector import PRODUCER_SOURCES


def _chips_for_selection(selection) -> QuestionChips | None:
    if selection is None or selection.question_id is None:
        return None
    if selection.source not in PRODUCER_SOURCES:
        return None
    return QuestionChips(question_id=selection.question_id)
```

In `handle_session_start` return:

```python
return SessionStartResult(
    opener=(...),
    phase=state.person_phase,
    selected_question_id=(
        state.selection.question_id if state.selection else None
    ),
    taps=[],
    chips=_chips_for_selection(state.selection),
)
```

In `handle_turn` return (find it; same pattern — return `chips=_chips_for_selection(state.selection)`).

- [ ] **Step 3: Update HTTP models**

In `src/flashback/http/models.py`:

```python
from flashback.orchestrator.protocol import QuestionChips as _QuestionChips


class QuestionChipsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    actions: list[Literal["skip", "suppress", "defer"]]


class QuestionDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    action: Literal["skip", "suppress", "defer"]


class SessionStartMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["starter", "steady"]
    selected_question_id: UUID | None = None
    taps: list[Tap] = Field(default_factory=list)
    question_chips: QuestionChipsOut | None = None


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    role_id: UUID
    message: str = Field(min_length=1, max_length=8000)
    question_decision: QuestionDecisionInput | None = None


class TurnMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    emotional_temperature: Literal["low", "medium", "high"] | None = None
    segment_boundary: bool = False
    taps: list[Tap] = Field(default_factory=list)
    question_chips: QuestionChipsOut | None = None
```

- [ ] **Step 4: Update `/session/start` route to surface chips**

In `src/flashback/http/routes/session.py`, replace the return:

```python
return SessionStartResponse(
    session_id=body.session_id,
    opener=result.opener,
    metadata=SessionStartMetadata(
        phase=result.phase,
        selected_question_id=result.selected_question_id,
        taps=result.taps,
        question_chips=(
            QuestionChipsOut(
                question_id=result.chips.question_id,
                actions=result.chips.actions,
            )
            if result.chips else None
        ),
    ),
)
```

- [ ] **Step 5: Update `/turn` route to consume decisions and emit chips**

In `src/flashback/http/routes/turn.py`, before calling the orchestrator, persist any incoming decision:

```python
if body.question_decision is not None:
    repo = QuestionDecisionRepository(db_pool)
    await repo.record(
        person_id=body.person_id,
        question_id=body.question_decision.question_id,
        action=body.question_decision.action,
    )
    # Also: stamp WM with this question_id so the next selector excludes it
    # via recent_ids — defends against race where decision is recorded but
    # not yet reflected by the eligibility query in the same turn.
    await wm.append_asked_question(
        session_id=str(body.session_id),
        question_id=str(body.question_decision.question_id),
    )
```

Surface chips in the response (mirror session-start change above).

- [ ] **Step 6: Update orchestrator's `handle_turn` signature OR pass the decision via state**

Decide: simpler to record the decision in the route layer (as above) than to thread it through the orchestrator pipeline. Keep the orchestrator pure; the route is the one persistence point.

- [ ] **Step 7: Tests for route changes**

Add to `tests/http/test_turn.py`:

```python
@pytest.mark.asyncio
async def test_turn_records_question_decision(client, db_pool, person, producer_question):
    response = await client.post(
        "/turn",
        json={
            "session_id": str(uuid4()),
            "person_id": str(person.id),
            "role_id": str(uuid4()),
            "message": "next topic please",
            "question_decision": {
                "question_id": str(producer_question.id),
                "action": "skip",
            },
        },
        headers={"X-Service-Token": "test-token"},
    )
    assert response.status_code == 200
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT action FROM active_question_decisions "
                "WHERE question_id = %s AND person_id = %s",
                (producer_question.id, person.id),
            )
            row = await cur.fetchone()
    assert row[0] == "skip"
```

- [ ] **Step 8: Run all tests**

Run: `pytest tests/ -v`
Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add src/flashback tests
git commit -m "feat(api): QuestionChips on responses + question_decision input on /turn"
```

---

## Task 10: Local-dev UI — render chips, POST decision

**Files:**
- Modify: `local/server.py`
- Modify: `local/static/index.html`
- Modify: `local/static/app.js` (or whatever JS file owns the chat UI)

- [ ] **Step 1: Add a proxy route in `local/server.py`**

The local server probably already proxies `/session/start` and `/turn` — they will naturally carry the new fields. Verify the response model passthrough works (FastAPI's response_model should not drop unknown fields if the local proxy uses `dict` passthrough).

If the local proxy strips fields via a typed model, add `question_chips` to that model.

- [ ] **Step 2: Render chips in `local/static/index.html`**

In the chat message renderer, when an assistant message arrives with `metadata.question_chips`, append a row of three buttons under the message body:

```html
<div class="question-chip-row" data-question-id="{{question_id}}">
  <button class="question-chip" data-action="skip">Skip</button>
  <button class="question-chip" data-action="defer">I'll tell you later</button>
  <button class="question-chip" data-action="suppress">Don't ask me again</button>
</div>
```

Style: small text buttons under the bot bubble, not overlaying the input.

- [ ] **Step 3: Wire chip clicks to the next `/turn` call**

In `local/static/app.js`:

```javascript
let pendingDecision = null;

function attachChipHandlers(messageEl) {
  const row = messageEl.querySelector('.question-chip-row');
  if (!row) return;
  const questionId = row.dataset.questionId;
  row.querySelectorAll('.question-chip').forEach((btn) => {
    btn.addEventListener('click', () => {
      pendingDecision = { question_id: questionId, action: btn.dataset.action };
      row.querySelectorAll('.question-chip').forEach((b) => (b.disabled = true));
      btn.classList.add('chip-selected');
      // Optional: auto-send an empty turn that just carries the decision
      // so the next bot question surfaces immediately.
      sendTurn('');
    });
  });
}

async function sendTurn(message) {
  const body = {
    session_id: SESSION_ID,
    person_id: PERSON_ID,
    role_id: ROLE_ID,
    message: message || '(skipped)',
  };
  if (pendingDecision) {
    body.question_decision = pendingDecision;
    pendingDecision = null;
  }
  const r = await fetch('/api/turn', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  // ... existing rendering ...
}
```

The empty `message: '(skipped)'` sentinel preserves the `min_length=1` invariant on `TurnRequest.message`. (If you'd rather relax the validator, do that in `models.py` — but a sentinel value is simpler and keeps the audit trail.)

- [ ] **Step 4: Verify chips render and persist by hand**

Run: start the local server and a session in starter phase with at least one producer-bank question seeded.
Expected:
1. Opener mentions the seeded question.
2. Three chips render under the message.
3. Click "Skip" → next turn arrives; chips are disabled on the prior message; next bot question is different.
4. Reload page (new session) → the same skipped question should NOT re-appear unless it's the only candidate.
5. Click "Don't ask me again" on a question → it never re-appears.

- [ ] **Step 5: Commit**

```bash
git add local/
git commit -m "feat(local): question-decision chips in chat UI"
```

---

## Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `API.md`
- Modify: `SCHEMA.md`

- [ ] **Step 1: Add invariant entry to `CLAUDE.md` § 4**

Append a new numbered invariant (23) describing question decisions:

```markdown
23. **Question decisions are explicit and durable.** The
    `question_decisions` table captures per-(person, question) user
    intent from the chip surface in three actions: `skip`, `suppress`,
    `defer`. Eligibility queries (steady candidates + coverage taps)
    exclude `suppress` permanently and `skip` with a 3-step fallback
    (skip-excluded → unfiltered if pool empty → skipped-allowed if
    still empty). `defer` excludes for the current session and adds
    `DEFER_BOOST` to `combined_score` in the next session. The chip
    surface fires only for producer-bank sources (`dropped_reference`,
    `underdeveloped_entity`, `thread_deepen`, `life_period_gap`,
    `universal_dimension`); coverage taps keep their own chip surface
    and archetype questions (onboarding + theme unlock) are exempt.
    Decisions arrive on the next `/turn` via the optional
    `question_decision` field, persisted before the turn pipeline runs.
```

- [ ] **Step 2: Add ranking note to invariants**

Append:

```markdown
24. **`combined_score` includes a recency term and a defer-boost.**
    Source priority remains the dominant signal at equal age, but a
    fresh lower-tier question can outrank an old higher-tier one when
    the age gap is large (see `RECENCY_WEIGHT` and
    `RECENCY_HALF_LIFE_DAYS` in `phase_gate/ranking.py`). The selector
    also applies a session-scoped same-source cooldown: it skips the
    last-asked source when an alternative exists, falling back when
    the cooldown would empty the candidate pool.
```

- [ ] **Step 3: Update `API.md`**

Add request/response snippets:
- `TurnRequest.question_decision`
- `TurnResponse.metadata.question_chips`
- `SessionStartResponse.metadata.question_chips`

Match the exact shape from `http/models.py`.

- [ ] **Step 4: Update `SCHEMA.md`**

Document the `question_decisions` table and `active_question_decisions` view.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md API.md SCHEMA.md
git commit -m "docs: question decisions + recency ranking"
```

---

## Self-review checklist

- [x] Spec coverage: all three pillars from the user's design have tasks — chips/edges (Tasks 1–3, 9, 10), recency (Task 4), per-source cooldown (Tasks 5–7).
- [x] Session-start opener carries a producer-bank question in starter phase (Task 8).
- [x] Coverage taps (P0) keep their existing tap-card surface — chip surface fires only for producer-bank sources via `PRODUCER_SOURCES` filter in `_chips_for_selection`.
- [x] `promote_seeded_to_tap` becomes a no-op for producer-bank sources (Task 7 Step 4), so producer-bank questions in starter phase are inlined with chips, not promoted to tap cards. Two-skip-buttons problem avoided.
- [x] No placeholders.
- [x] Type names consistent: `Action`, `QuestionDecision`, `QuestionDecisionRepository`, `QuestionChips`, `QuestionChipsOut`, `QuestionDecisionInput`, `RECENCY_WEIGHT`, `DEFER_BOOST`, `PRODUCER_SOURCES`.
- [x] Function signatures match across tasks (`combined_score` keyword-only `created_at` / `now` / `is_deferred`; `select_next_question` and `select` accept `last_seeded_source`).

---

## Open issues / risks

1. **Decisions race with the same-turn response.** The user taps "Skip" and the same `/turn` call records the decision AND picks the next question. The new selector reads from `active_question_decisions` and excludes the skipped row in the same call — Postgres guarantees this within the session because the INSERT runs before the SELECT. Verify in Task 9 Step 5 test.
2. **Session-start in starter phase calls the bank with empty `recent_ids`.** If the only producer-bank question is `suppressed`, the SELECT returns nothing and the opener falls back to LLM-only. This is the correct behavior.
3. **The opener LLM may paraphrase the anchor question.** The chip surface still references the canonical `question_id`. If the user replies in a way that doesn't match the paraphrase, the segment detector may not associate the answer with the question. This is the existing risk for any seeded question; the chip surface doesn't change it.
4. **`suppress` is permanent in this plan.** No admin endpoint to clear. Document this in `CLAUDE.md`; add an endpoint later if product wants it.
