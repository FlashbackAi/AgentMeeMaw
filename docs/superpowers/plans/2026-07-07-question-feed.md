# Question Feed → Tap-to-Seed Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the producer-bank questions we hold for a person as a browsable, ranked feed (`GET /questions/feed`), and let tapping one start a session whose opener grounds on that exact question (`session_metadata.question_id`).

**Architecture:** Piece 1 refactors the existing `SteadySelector` score-and-sort core into two shared module functions (`fetch_steady_candidates`, `rank_candidates`), then a thin `QuestionFeed` service reuses them, applies the invariant #10 universal-dimension diversity spread, and caps the slice; a new FastAPI route exposes it. Piece 2 adds an `apply_picked_question` orchestrator step (mirroring `apply_theme_unlock`) that, when `session_metadata.question_id` is set, loads that question and sets `state.selection` so the opener anchors on it — bypassing selectors and cooldown/recency dedup entirely.

**Tech Stack:** Python 3, FastAPI, psycopg (async), pydantic, pytest. Postgres via `AsyncConnectionPool`.

## Global Constraints

- **Filter `status='active'`** in every canonical-table query (invariant #1). Prefer the `active_questions` view.
- **Always filter `person_id`** (invariant #2). Never cross legacies.
- **Producer-bank sources only** in the feed: `dropped_reference`, `underdeveloped_entity`, `thread_deepen`, `life_period_gap`, `universal_dimension` (the existing `PRODUCER_SOURCES` tuple in `steady_selector.py`).
- **Universal-dimension diversity** (invariant #10): at most 1 `universal_dimension` question per any window of 5 in the returned feed.
- **`question_decisions` honored** (invariant #23): `skip`/`suppress` excluded via the existing `SELECT_STEADY_CANDIDATES` query with `exclude_skipped=True`; `defer` stays in and keeps its `DEFER_BOOST`.
- **The tap is an explicit pick** — exempt from same-source cooldown and recency dedup. Always honor the exact `question_id` tapped.
- **No new LLM calls.** Feed and seed are pure code.
- **No auth** (invariant #8). Routes sit behind `require_service_token` like every other route.
- **Docs in lockstep** (CLAUDE.md §10): `API.md`, `NODE_INTEGRATION.md`, `CLAUDE.md` update with the code.
- **Commit co-author trailer:** `Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>` — never the Opus trailer.
- Work happens on branch `feat/question-feed` (already checked out).

---

## File Structure

- **Modify** `src/flashback/phase_gate/steady_selector.py` — extract `fetch_steady_candidates()` and `rank_candidates()` as module-level functions; `SteadySelector` calls them. `_Candidate` / `_ScoredCandidate` become module-public (drop underscore) so the feed can consume them.
- **Create** `src/flashback/phase_gate/feed.py` — `QuestionFeed` service: fetch → rank → diversity spread → cap. `FeedQuestion` result dataclass.
- **Modify** `src/flashback/http/models.py` — `FeedQuestionOut`, `QuestionFeedResponse` pydantic models.
- **Create** `src/flashback/http/routes/questions.py` — `GET /questions/feed` route.
- **Modify** `src/flashback/http/app.py` — register the questions router.
- **Create** `src/flashback/orchestrator/steps/apply_picked_question.py` — the seed step.
- **Modify** `src/flashback/orchestrator/steps/__init__.py` — export `apply_picked_question`.
- **Modify** `src/flashback/orchestrator/orchestrator.py` — wire the step into `handle_session_start` and `handle_session_start_stream`.
- **Modify** `API.md`, `NODE_INTEGRATION.md`, `CLAUDE.md` — contract docs.
- **Create/Modify tests:** `tests/phase_gate/test_steady_selector.py` (refactor still green), `tests/phase_gate/test_feed.py` (new), `tests/orchestrator/steps/test_apply_picked_question.py` (new), `tests/http/test_questions_feed.py` (new).

---

## Task 1: Extract shared candidate-fetch and ranking helpers

Pull the score-and-sort core out of `SteadySelector` so both the selector and the feed use one code path. No behavior change to the single-pick path.

**Files:**
- Modify: `src/flashback/phase_gate/steady_selector.py`
- Test: `tests/phase_gate/test_steady_selector.py` (existing, must stay green), `tests/phase_gate/test_feed.py` (new unit test for `rank_candidates`)

**Interfaces:**
- Produces:
  - `Candidate` (renamed from `_Candidate`, same fields + `themes` property) — a frozen dataclass.
  - `ScoredCandidate` (renamed from `_ScoredCandidate`): `.candidate: Candidate`, `.score: float`.
  - `async def fetch_steady_candidates(pool: AsyncConnectionPool, person_id: UUID, recent_ids: list[UUID], sources: tuple[str, ...], *, exclude_skipped: bool) -> list[Candidate]`
  - `def rank_candidates(candidates: list[Candidate], *, recent_themes: set[str], active_theme_slug: str | None, now: datetime) -> list[ScoredCandidate]` — returns the full list sorted by `(score, candidate.created_at)` descending.

- [ ] **Step 1: Write the failing unit test for `rank_candidates`**

Create `tests/phase_gate/test_feed.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from flashback.phase_gate.steady_selector import Candidate, rank_candidates

NOW = datetime(2026, 5, 4, tzinfo=timezone.utc)


def _cand(qid: str, source: str, created_at: datetime, themes=None) -> Candidate:
    return Candidate(
        id=UUID(qid),
        text=f"q-{qid[:4]}",
        source=source,
        attributes={"themes": themes or []},
        created_at=created_at,
    )


def test_rank_candidates_orders_by_score_desc():
    old_high = _cand(
        "33333333-3333-3333-3333-333333333333",
        "dropped_reference",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    fresh_low = _cand(
        "44444444-4444-4444-4444-444444444444",
        "universal_dimension",
        NOW,
    )
    ranked = rank_candidates(
        [fresh_low, old_high],
        recent_themes=set(),
        active_theme_slug=None,
        now=NOW,
    )
    assert [sc.candidate.id for sc in ranked] == [old_high.id, fresh_low.id]
    assert ranked[0].score >= ranked[1].score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/phase_gate/test_feed.py::test_rank_candidates_orders_by_score_desc -v`
Expected: FAIL with `ImportError: cannot import name 'Candidate'` (or `rank_candidates`).

- [ ] **Step 3: Refactor `steady_selector.py`**

Rename `_Candidate` → `Candidate` and `_ScoredCandidate` → `ScoredCandidate` (keep them where they are, drop the leading underscore, update all references in the file including `_apply_universal_dimension_demotion`'s type hints).

Add these two module-level functions (place them above the `SteadySelector` class). Import `AsyncConnectionPool` is already present; add `from flashback.phase_gate.queries import SELECT_STEADY_CANDIDATES` is already imported.

```python
async def fetch_steady_candidates(
    pool: AsyncConnectionPool,
    person_id: UUID,
    recent_ids: list[UUID],
    sources: tuple[str, ...],
    *,
    exclude_skipped: bool,
) -> list[Candidate]:
    async with pool.connection() as conn:
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
        Candidate(
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


def rank_candidates(
    candidates: list[Candidate],
    *,
    recent_themes: set[str],
    active_theme_slug: str | None,
    now: datetime,
) -> list[ScoredCandidate]:
    scored = [
        ScoredCandidate(
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
    scored.sort(
        key=lambda item: (item.score, item.candidate.created_at),
        reverse=True,
    )
    return scored
```

Now rewire `SteadySelector`:
- Replace the body of `SteadySelector._fetch_candidates` so it delegates: `return await fetch_steady_candidates(self._pool, person_id, recent_ids, sources, exclude_skipped=exclude_skipped)`.
- In `SteadySelector.select`, replace the inline `scored = [...]; scored.sort(...)` block (the `now = datetime.now(...)` line through the `scored.sort(...)` call) with:

```python
        now = datetime.now(timezone.utc)
        scored = rank_candidates(
            candidates,
            recent_themes=recent_themes,
            active_theme_slug=active_theme_slug,
            now=now,
        )
```

Leave `_apply_universal_dimension_demotion(scored)` and everything after it unchanged (only the type names `_ScoredCandidate` → `ScoredCandidate`).

- [ ] **Step 4: Run the new test and the full steady-selector suite**

Run: `pytest tests/phase_gate/test_feed.py tests/phase_gate/test_steady_selector.py -v`
Expected: PASS (new test passes; all existing steady-selector tests stay green — the refactor is behavior-preserving).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/phase_gate/steady_selector.py tests/phase_gate/test_feed.py
git commit -m "$(cat <<'EOF'
refactor(phase_gate): extract fetch_steady_candidates + rank_candidates

Pull the score-and-sort core out of SteadySelector into module functions
so the upcoming question feed reuses one ranking code path. Behavior of
the single-pick path is unchanged.

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
EOF
)"
```

---

## Task 2: `QuestionFeed` service (fetch → rank → diversity → cap)

**Files:**
- Create: `src/flashback/phase_gate/feed.py`
- Test: `tests/phase_gate/test_feed.py` (extend)

**Interfaces:**
- Consumes: `fetch_steady_candidates`, `rank_candidates`, `Candidate`, `ScoredCandidate`, `PRODUCER_SOURCES` from `steady_selector.py`.
- Produces:
  - `FeedQuestion` frozen dataclass: `question_id: UUID`, `text: str`, `source: str`, `themes: list[str]`, `created_at: datetime`.
  - `class QuestionFeed` with `def __init__(self, db_pool: AsyncConnectionPool)` and `async def build(self, person_id: UUID, *, limit: int = 25) -> list[FeedQuestion]`.
  - `def spread_universal_dimension(scored: list[ScoredCandidate], *, window: int = 5, max_per_window: int = 1) -> list[ScoredCandidate]` — reorders so no window of `window` consecutive items holds more than `max_per_window` `universal_dimension` entries. Never drops items; only defers them later in the list.

- [ ] **Step 1: Write the failing tests for diversity spread and the service cap**

Append to `tests/phase_gate/test_feed.py`:

```python
from flashback.phase_gate.feed import (
    FeedQuestion,
    QuestionFeed,
    spread_universal_dimension,
)
from flashback.phase_gate.steady_selector import ScoredCandidate


def _scored(qid_char: str, source: str, score: float) -> ScoredCandidate:
    qid = UUID(qid_char * 8 + "-" + qid_char * 4 + "-" + qid_char * 4
               + "-" + qid_char * 4 + "-" + qid_char * 12)
    return ScoredCandidate(
        candidate=_cand(str(qid), source, NOW),
        score=score,
    )


def test_spread_universal_dimension_no_two_in_window_of_five():
    # Five universal_dimension items ranked highest, then one non-universal.
    scored = [
        _scored("1", "universal_dimension", 9.0),
        _scored("2", "universal_dimension", 8.0),
        _scored("3", "universal_dimension", 7.0),
        _scored("4", "underdeveloped_entity", 6.0),
        _scored("5", "universal_dimension", 5.0),
    ]
    out = spread_universal_dimension(scored, window=5, max_per_window=1)
    # No window of 5 consecutive positions may hold >1 universal_dimension.
    for start in range(0, max(1, len(out) - 4)):
        window = out[start:start + 5]
        universals = sum(
            1 for sc in window if sc.candidate.source == "universal_dimension"
        )
        assert universals <= 1
    # No item is dropped.
    assert len(out) == len(scored)


class _FeedPool:
    def __init__(self, rows):
        self._rows = rows

    def connection(self):
        return _FeedCtx(_FeedConn(self._rows))


class _FeedConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FeedCtx(_FeedCursor(self._rows))


class _FeedCursor:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, sql, params=None):
        self._sql = sql

    async def fetchall(self):
        return self._rows


class _FeedCtx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


async def test_question_feed_build_caps_and_maps():
    rows = [
        (
            UUID("33333333-3333-3333-3333-333333333333"),
            "Tell me about the bike.",
            "dropped_reference",
            {"themes": ["family"]},
            NOW,
            None,
            None,
        ),
        (
            UUID("44444444-4444-4444-4444-444444444444"),
            "What was your first job?",
            "life_period_gap",
            {"themes": ["career"]},
            NOW,
            None,
            None,
        ),
    ]
    feed = QuestionFeed(_FeedPool(rows))
    result = await feed.build(
        UUID("11111111-1111-1111-1111-111111111111"), limit=1
    )
    assert len(result) == 1
    assert isinstance(result[0], FeedQuestion)
    assert result[0].source == "dropped_reference"
    assert result[0].themes == ["family"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/phase_gate/test_feed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flashback.phase_gate.feed'`.

- [ ] **Step 3: Implement `src/flashback/phase_gate/feed.py`**

```python
"""Read-only ranked feed of a person's producer-bank questions.

Reuses the SteadySelector fetch + ranking so the feed's ordering matches
what the bot would pick next. Applies the invariant #10 universal-
dimension spread across the returned slice, then caps. No LLM, no session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.phase_gate.steady_selector import (
    PRODUCER_SOURCES,
    Candidate,
    ScoredCandidate,
    fetch_steady_candidates,
    rank_candidates,
)

DEFAULT_LIMIT = 25
MAX_LIMIT = 50


@dataclass(frozen=True)
class FeedQuestion:
    question_id: UUID
    text: str
    source: str
    themes: list[str]
    created_at: datetime


def spread_universal_dimension(
    scored: list[ScoredCandidate],
    *,
    window: int = 5,
    max_per_window: int = 1,
) -> list[ScoredCandidate]:
    """Reorder so no window of ``window`` consecutive positions holds more
    than ``max_per_window`` ``universal_dimension`` items. Never drops."""
    universals = [
        sc for sc in scored if sc.candidate.source == "universal_dimension"
    ]
    others = [
        sc for sc in scored if sc.candidate.source != "universal_dimension"
    ]
    if not universals:
        return list(scored)

    out: list[ScoredCandidate] = []
    ui = 0
    oi = 0
    while ui < len(universals) or oi < len(others):
        recent = out[-window + 1:] if window > 1 else []
        recent_universals = sum(
            1 for sc in recent if sc.candidate.source == "universal_dimension"
        )
        can_place_universal = recent_universals < max_per_window
        if oi < len(others) and (
            not can_place_universal or ui >= len(universals)
        ):
            out.append(others[oi])
            oi += 1
        elif ui < len(universals) and can_place_universal:
            out.append(universals[ui])
            ui += 1
        elif oi < len(others):
            out.append(others[oi])
            oi += 1
        else:
            # Only universals remain and the window is saturated; append the
            # rest in rank order (tail spread is best-effort — never drop).
            out.extend(universals[ui:])
            ui = len(universals)
    return out


class QuestionFeed:
    def __init__(self, db_pool: AsyncConnectionPool) -> None:
        self._pool = db_pool

    async def build(
        self, person_id: UUID, *, limit: int = DEFAULT_LIMIT
    ) -> list[FeedQuestion]:
        limit = max(1, min(limit, MAX_LIMIT))
        candidates: list[Candidate] = await fetch_steady_candidates(
            self._pool,
            person_id,
            [],
            PRODUCER_SOURCES,
            exclude_skipped=True,
        )
        ranked = rank_candidates(
            candidates,
            recent_themes=set(),
            active_theme_slug=None,
            now=datetime.now(timezone.utc),
        )
        spread = spread_universal_dimension(ranked)
        return [
            FeedQuestion(
                question_id=sc.candidate.id,
                text=sc.candidate.text,
                source=sc.candidate.source,
                themes=sorted(sc.candidate.themes),
                created_at=sc.candidate.created_at,
            )
            for sc in spread[:limit]
        ]
```

Note: `SELECT_STEADY_CANDIDATES` already caps at `LIMIT 50` in SQL, so `MAX_LIMIT = 50` is consistent with the underlying pool size.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/phase_gate/test_feed.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/phase_gate/feed.py tests/phase_gate/test_feed.py
git commit -m "$(cat <<'EOF'
feat(phase_gate): QuestionFeed service with universal-dimension spread

Ranked, capped feed of producer-bank questions reusing SteadySelector's
fetch + ranking; spreads universal_dimension so no window of 5 holds more
than one (invariant #10). Honors skip/suppress, boosts defer.

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
EOF
)"
```

---

## Task 3: `GET /questions/feed` route

**Files:**
- Modify: `src/flashback/http/models.py`
- Create: `src/flashback/http/routes/questions.py`
- Modify: `src/flashback/http/app.py`
- Test: `tests/http/test_questions_feed.py`

**Interfaces:**
- Consumes: `QuestionFeed` from `flashback.phase_gate.feed`; `get_db_pool` from `flashback.http.deps`; `require_service_token` from `flashback.http.auth`.
- Produces:
  - `FeedQuestionOut` (pydantic): `question_id: UUID`, `text: str`, `source: str`, `themes: list[str]`, `created_at: datetime`.
  - `QuestionFeedResponse` (pydantic): `questions: list[FeedQuestionOut]`.
  - Route `GET /questions/feed?person_id=<uuid>&limit=<int>`.

- [ ] **Step 1: Write the failing route test**

Create `tests/http/test_questions_feed.py`. Model it on the existing DB-touching HTTP tests (`tests/http/conftest.py` provides `client_with_db` + `async_db_pool`; the token header helper is `auth_headers()` returning `{"X-Service-Token": "test-token"}`; tests skip when `TEST_DATABASE_URL` is unset). Seed two active questions directly, then hit the endpoint.

```python
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set; skipping DB-touching HTTP test.",
)


async def _seed_person_and_questions(async_db_pool):
    person_id = uuid4()
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO persons (id, name, relationship, phase)
                VALUES (%s, %s, %s, 'steady')
                """,
                (str(person_id), "Ishita", "mother"),
            )
            await cur.execute(
                """
                INSERT INTO questions (id, person_id, text, source, status, attributes)
                VALUES
                  (%s, %s, %s, 'dropped_reference', 'active', %s),
                  (%s, %s, %s, 'life_period_gap',   'active', %s)
                """,
                (
                    str(uuid4()), str(person_id), "Tell me about the bike.",
                    '{"themes": ["family"]}',
                    str(uuid4()), str(person_id), "What was your first job?",
                    '{"themes": ["career"]}',
                ),
            )
        await conn.commit()
    return person_id


async def test_questions_feed_returns_ranked_producer_questions(
    client_with_db, async_db_pool
):
    person_id = await _seed_person_and_questions(async_db_pool)
    resp = await client_with_db.get(
        f"/questions/feed?person_id={person_id}",
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    sources = {q["source"] for q in body["questions"]}
    assert sources == {"dropped_reference", "life_period_gap"}
    # dropped_reference outranks life_period_gap at equal age.
    assert body["questions"][0]["source"] == "dropped_reference"


async def test_questions_feed_empty_for_unknown_person(client_with_db):
    resp = await client_with_db.get(
        f"/questions/feed?person_id={uuid4()}",
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"questions": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/http/test_questions_feed.py -v`
Expected: FAIL — 404 (route not registered) or collection error.

- [ ] **Step 3: Add pydantic models to `src/flashback/http/models.py`**

Append near the other response models:

```python
class FeedQuestionOut(BaseModel):
    question_id: UUID
    text: str
    source: str
    themes: list[str]
    created_at: datetime


class QuestionFeedResponse(BaseModel):
    questions: list[FeedQuestionOut]
```

Ensure `UUID`, `datetime`, and `BaseModel` are already imported in that file (they are used elsewhere; add imports only if missing).

- [ ] **Step 4: Implement `src/flashback/http/routes/questions.py`**

```python
"""``GET /questions/feed`` — ranked producer-bank question feed.

Read-only browse surface. Ranking is agent-side computation (the API.md
§9 carve-out), so it lives here rather than as a raw Node view read.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool
from flashback.http.models import FeedQuestionOut, QuestionFeedResponse
from flashback.phase_gate.feed import DEFAULT_LIMIT, MAX_LIMIT, QuestionFeed

router = APIRouter(
    prefix="/questions",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.questions")


@router.get("/feed", response_model=QuestionFeedResponse)
async def questions_feed(
    person_id: UUID = Query(...),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> QuestionFeedResponse:
    structlog.contextvars.bind_contextvars(person_id=str(person_id))
    feed = QuestionFeed(db_pool)
    questions = await feed.build(person_id, limit=limit)
    log.info("questions.feed", count=len(questions))
    return QuestionFeedResponse(
        questions=[
            FeedQuestionOut(
                question_id=q.question_id,
                text=q.text,
                source=q.source,
                themes=q.themes,
                created_at=q.created_at,
            )
            for q in questions
        ]
    )
```

- [ ] **Step 5: Register the router in `src/flashback/http/app.py`**

Add the import alongside the other route imports (match the existing `from flashback.http.routes.X import router as X_router` style):

```python
from flashback.http.routes.questions import router as questions_router
```

Add the registration next to the others (after `session_router` is a natural spot):

```python
    app.include_router(questions_router)
```

- [ ] **Step 6: Run to verify pass**

Run: `pytest tests/http/test_questions_feed.py -v`
Expected: PASS (skipped if `TEST_DATABASE_URL` unset — if so, run once with the test DB per the verify skill / test_environment notes).

- [ ] **Step 7: Commit**

```bash
git add src/flashback/http/models.py src/flashback/http/routes/questions.py src/flashback/http/app.py tests/http/test_questions_feed.py
git commit -m "$(cat <<'EOF'
feat(http): GET /questions/feed ranked producer-bank feed

Read-only browse surface backed by QuestionFeed. person_id-scoped,
limit-clamped, behind the service token.

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
EOF
)"
```

---

## Task 4: `apply_picked_question` orchestrator step + pipeline wiring

**Files:**
- Create: `src/flashback/orchestrator/steps/apply_picked_question.py`
- Modify: `src/flashback/orchestrator/steps/__init__.py`
- Modify: `src/flashback/orchestrator/orchestrator.py`
- Test: `tests/orchestrator/steps/test_apply_picked_question.py`

**Interfaces:**
- Consumes: `SessionStartState` (has `.session_metadata: dict`, `.person_id`, `.selection: SelectionResult | None`, `.person_phase`); `OrchestratorDeps` (has `.db_pool`); `SelectionResult` from `flashback.phase_gate.schema`.
- Produces: `async def apply_picked_question(state: SessionStartState, deps: OrchestratorDeps) -> None`.

Behavior: when `state.session_metadata["question_id"]` is set, load the active, person-scoped question and set `state.selection` to a `SelectionResult(phase=state.person_phase, question_id=..., question_text=..., source=..., rationale="explicit feed pick")`. Runs after `select_starter_question`, so it overrides any auto pick. Unknown/foreign/inactive id → log and return, leaving `state.selection` as-is (graceful degrade). Must never raise.

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/steps/test_apply_picked_question.py`:

```python
from __future__ import annotations

from uuid import UUID

import pytest

from flashback.orchestrator.state import SessionStartState
from flashback.orchestrator.steps.apply_picked_question import apply_picked_question

PERSON_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ROLE_ID = UUID("99999999-9999-9999-9999-999999999999")
QID = UUID("33333333-3333-3333-3333-333333333333")


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def execute(self, sql, params=None):
        self._sql = sql

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Ctx(_Cursor(self._row))


class _Ctx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, row):
        self._row = row

    def connection(self):
        return _Ctx(_Conn(self._row))


class _Deps:
    def __init__(self, row):
        self.db_pool = _Pool(row)


def _state(metadata, phase="steady"):
    st = SessionStartState(
        session_id=SESSION_ID,
        person_id=PERSON_ID,
        role_id=ROLE_ID,
        session_metadata=metadata,
        started_at=None,
        mode="text",
    )
    st.person_phase = phase
    return st


async def test_picked_question_sets_selection():
    row = (QID, "Tell me about the bike.", "dropped_reference")
    state = _state({"question_id": str(QID)})
    await apply_picked_question(state, _Deps(row))
    assert state.selection is not None
    assert state.selection.question_id == QID
    assert state.selection.question_text == "Tell me about the bike."
    assert state.selection.source == "dropped_reference"


async def test_no_question_id_is_noop():
    state = _state({})
    await apply_picked_question(state, _Deps(None))
    assert state.selection is None


async def test_unknown_question_id_degrades():
    state = _state({"question_id": str(QID)})
    await apply_picked_question(state, _Deps(None))  # fetchone -> None
    assert state.selection is None
```

Confirm the `SessionStartState` constructor kwargs match `src/flashback/orchestrator/state.py` (fields: `session_id`, `person_id`, `role_id`, `session_metadata`, `started_at`, `mode`). If `started_at=None` is rejected, pass `datetime.now(timezone.utc)`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/orchestrator/steps/test_apply_picked_question.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashback.orchestrator.steps.apply_picked_question'`.

- [ ] **Step 3: Implement the step**

Create `src/flashback/orchestrator/steps/apply_picked_question.py`:

```python
"""Seed a session opener from an explicitly-picked feed question.

When the caller passes ``question_id`` in ``session_metadata`` (the
contributor tapped a question in the feed), load that active,
person-scoped question and set ``state.selection`` so the opener anchors
on it. This bypasses the selectors and any cooldown/recency dedup — an
explicit pick is always honored exactly.

Runs after ``select_starter_question`` so an explicit pick overrides an
auto-selected starter question, in both starter and steady phase. Never
raises: a bad id degrades to the normal opener.
"""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState
from flashback.phase_gate.schema import SelectionResult

log = structlog.get_logger("flashback.orchestrator.apply_picked_question")

_SELECT_PICKED_QUESTION = """
SELECT id, text, source
FROM active_questions
WHERE id = %(question_id)s
  AND person_id = %(person_id)s
"""


async def apply_picked_question(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    raw_question_id = state.session_metadata.get("question_id")
    if not raw_question_id:
        return

    with timed_step(log, "apply_picked_question"):
        try:
            async with deps.db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        _SELECT_PICKED_QUESTION,
                        {
                            "question_id": str(raw_question_id),
                            "person_id": str(state.person_id),
                        },
                    )
                    row = await cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "picked_question.lookup_failed",
                question_id=str(raw_question_id),
                error=type(exc).__name__,
            )
            return

        if row is None:
            log.warning(
                "picked_question.not_found",
                question_id=str(raw_question_id),
                person_id=str(state.person_id),
            )
            return

        question_id, text, source = row
        state.selection = SelectionResult(
            phase=state.person_phase if state.person_phase in ("starter", "steady")
            else "steady",
            question_id=question_id,
            question_text=text,
            source=source,
            rationale="explicit feed pick",
        )
        log.info(
            "picked_question.selected",
            question_id=str(question_id),
            source=source,
        )
```

- [ ] **Step 4: Export from `steps/__init__.py`**

In `src/flashback/orchestrator/steps/__init__.py`, add to the imports and `__all__` list (mirror how `apply_theme_unlock` is listed):

```python
from flashback.orchestrator.steps.apply_picked_question import apply_picked_question
```

and add `"apply_picked_question"` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/orchestrator/steps/test_apply_picked_question.py -v`
Expected: PASS (all three tests).

- [ ] **Step 6: Wire into both session-start pipelines**

In `src/flashback/orchestrator/orchestrator.py`:

Add `apply_picked_question` to the steps import block (near line 44 where `apply_theme_unlock` is imported).

In `handle_session_start`, insert a step between `select_starter_question` (ends ~line 140) and `generate_opener` (starts ~line 141), inside the `if self._deps.response_generator is not None:` block:

```python
                await execute(
                    policies=SESSION_START_POLICIES,
                    step_name="apply_picked_question",
                    fn=lambda: apply_picked_question(state, self._deps),
                    state=state,
                )
```

Do the exact same insertion in `handle_session_start_stream` — between its `select_starter_question` step (~line 683) and whatever generates/streams the opener next. Match the surrounding `await execute(...)` shape used there.

- [ ] **Step 7: Run the orchestrator session-start suite**

Run: `pytest tests/orchestrator/ -v -k "session_start or with_phase_gate or picked_question"`
Expected: PASS — no regressions in existing session-start behavior; the step is a no-op when `question_id` is absent (every existing test).

- [ ] **Step 8: Commit**

```bash
git add src/flashback/orchestrator/steps/apply_picked_question.py src/flashback/orchestrator/steps/__init__.py src/flashback/orchestrator/orchestrator.py tests/orchestrator/steps/test_apply_picked_question.py
git commit -m "$(cat <<'EOF'
feat(orchestrator): seed opener from picked feed question

apply_picked_question reads session_metadata.question_id, loads the
active person-scoped question, and sets state.selection so the opener
anchors on the tapped question. Runs after select_starter_question in
both the JSON and streaming session-start pipelines; degrades to the
normal opener on a bad id.

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
EOF
)"
```

---

## Task 5: Contract docs (API.md, NODE_INTEGRATION.md, CLAUDE.md)

**Files:**
- Modify: `API.md`, `NODE_INTEGRATION.md`, `CLAUDE.md`

No tests — documentation. One commit.

- [ ] **Step 1: `API.md`**

- In §9 "What this service does NOT expose", change the `GET /questions/...` line: it can no longer be a blanket "Node reads directly." Replace it with a note that raw `active_questions` reads stay Node-side, but the ranked feed is agent-served — and add a cross-reference to the new endpoint section.
- Add a new endpoint section documenting `GET /questions/feed?person_id=<uuid>&limit=<int, default 25, max 50>` with the response shape:

```jsonc
{
  "questions": [
    {
      "question_id": "uuid",
      "text": "string",
      "source": "dropped_reference | underdeveloped_entity | thread_deepen | life_period_gap | universal_dimension",
      "themes": ["family"],
      "created_at": "iso-8601"
    }
  ]
}
```

  Note: producer-bank sources only; ordered by the same `combined_score` the bot uses to pick next; `skip`/`suppress` excluded, `defer` boosted; universal-dimension spread ≤1 per 5; empty list for a fresh legacy.
- In the `POST /session/start` section, document the new optional `session_metadata.question_id` (UUID): "the feed question the contributor tapped; the opener anchors on it. Unknown/foreign id is ignored. Exempt from cooldown/recency dedup — always honored." Note it composes with `theme_id` (both may be present; both are soft context for the opener).

- [ ] **Step 2: `NODE_INTEGRATION.md`**

- In §6.4 "Tables Node reads, by surface", update the "Open questions / ask next" row: raw list still readable from `active_questions`, but the **ranked feed** is served by `GET /questions/feed` (agent-side ranking) — point Node at the endpoint for the scrolling feed.
- Add a short note on the tap→seed flow: Node passes the tapped `question_id` in `session_metadata` on `POST /session/start`; the agent seeds the opener. No new write surface.

- [ ] **Step 3: `CLAUDE.md` §9**

- Add a bullet for `GET /questions/feed` to the API-contract list (one or two lines, matching the style of the surrounding bullets).
- In the `POST /session/start` bullet, add `question_id` to the list of accepted `session_metadata` fields alongside `theme_id`, with a one-line description.

- [ ] **Step 4: Commit**

```bash
git add API.md NODE_INTEGRATION.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: contract for GET /questions/feed + session_metadata.question_id

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
EOF
)"
```

---

## Final verification

- [ ] **Run the full affected suites:**

Run: `pytest tests/phase_gate/ tests/orchestrator/ tests/http/test_questions_feed.py -v`
Expected: all PASS (DB-touching tests skipped only if `TEST_DATABASE_URL` unset — run them against the test DB per the `verify` skill before claiming done).

- [ ] **Manual smoke (verify skill):** start the dev stack, `GET /questions/feed?person_id=<a real dev person>`, confirm a ranked list; then `POST /session/start` with that `question_id` in `session_metadata` and confirm the opener opens on the tapped question.

- [ ] **Finish the branch** via superpowers:finishing-a-development-branch (PR or merge per user preference).
```
