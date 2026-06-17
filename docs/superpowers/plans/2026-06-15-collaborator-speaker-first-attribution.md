# Collaborator Speaker-first Retrieval + Attribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On recall turns, vector retrieval prefers the current contributor's own moments (soft bias), and any surfaced moment authored by a *different* contributor is credited to them by name in the prompt — never presented as the speaker's own.

**Architecture:** Read-path only, no migration. The current speaker's `user_id` (already on `TurnState` from sub-project 1) is threaded into the moment search (a soft additive distance bias in SQL) and into the response context (a `told_by` label on cross-contributor moments) plus a prompt instruction to credit. NULL/own moments stay neutral; single-contributor data is a guaranteed no-op.

**Tech Stack:** Python, psycopg (raw SQL) + pgvector, pydantic v2, FastAPI. Tests: pytest via `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-15-collaborator-speaker-first-attribution-design.md`

---

## Execution notes (READ FIRST)

- **NO COMMITS this cycle.** Do not run `git commit` / `git add` in any task.
  All changes accumulate in the working tree on branch
  `feature/collaborator-provenance`; the user commits/pushes later. Each
  task ends with a **verification step** (run tests + inspect
  `git diff -- <task files>`) instead of a commit.
- **Test command (always):**
  `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings <path>`.
  Never plain `pytest` (no system pytest on this machine).
- **Pre-existing baseline = 14 failures** (tests/workers/embedding x6,
  tests/retrieval/test_voyage.py::test_happy_path_returns_vector,
  tests/orchestrator: test_coverage_tap::test_no_tap_on_first_user_turn,
  test_orchestrator_with_phase_gate x3,
  test_segment_turn_sequence::test_five_turn_sequence_closes_one_segment,
  test_stub_with_retrieval x2). The full suite must stay at exactly these
  14 — adding zero new failures is the bar.
- **DB-gated tests can now run.** Export the test DB first (it is the
  migrated `flashback_test`):
  `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test`
  Caveat: the `schema_applied` fixture runs `DROP SCHEMA public CASCADE` and
  re-applies all migrations on that DB at session start, wiping any manual
  dev data in `flashback_test`. That is acceptable for this cycle. If
  `TEST_DATABASE_URL` is unset, the DB-gated tests skip (still fine — the
  no-DB tests carry most of the coverage).
- **NEVER run `git checkout`** (would lose uncommitted work).
- Reviewers isolate a task's change with `git diff -- <the task's files>`
  since there are no per-task commit SHAs.

## File map (all modifications; no new source files)

| File | Responsibility | Task |
|---|---|---|
| `src/flashback/retrieval/schema.py` | `MomentResult` carries provenance | 1 |
| `src/flashback/retrieval/queries.py` | `SEARCH_MOMENTS_SQL`: select provenance + soft-bias ORDER BY | 2 |
| `src/flashback/retrieval/service.py` | `SPEAKER_BIAS` const; `search_moments(current_user_id=…)` binds params | 2 |
| `src/flashback/orchestrator/steps/retrieve.py` | pass `state.user_id` into `search_moments` | 3 |
| `src/flashback/response_generator/schema.py` | `TurnContext.current_user_id` | 4 |
| `src/flashback/response_generator/context.py` | `render_turn_context` labels cross-contributor moments | 5 |
| `src/flashback/response_generator/prompts.py` | `RECALL_PROMPT` crediting instruction | 6 |
| `src/flashback/orchestrator/steps/generate_response.py` | wire `current_user_id` into `TurnContext` | 7 |

New test files: `tests/retrieval/test_speaker_first.py`,
`tests/response_generator/test_attribution_render.py`. Existing test files
extended as noted.

---

### Task 1: `MomentResult` carries provenance

**Files:**
- Modify: `src/flashback/retrieval/schema.py:12-23` (`MomentResult`)
- Test: `tests/retrieval/test_speaker_first.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/retrieval/test_speaker_first.py`:

```python
"""Speaker-first retrieval + provenance on MomentResult (sub-project 2)."""

from datetime import datetime, timezone
from uuid import uuid4

from flashback.retrieval.schema import MomentResult


def _moment_row(**overrides):
    row = {
        "id": uuid4(),
        "person_id": uuid4(),
        "title": "t",
        "narrative": "n",
        "time_anchor": None,
        "life_period_estimate": None,
        "sensory_details": None,
        "emotional_tone": None,
        "contributor_perspective": None,
        "created_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_moment_result_carries_told_by():
    uid = uuid4()
    m = MomentResult.model_validate(
        _moment_row(told_by_user_id=uid, told_by_display_name="Ravi")
    )
    assert m.told_by_user_id == uid
    assert m.told_by_display_name == "Ravi"


def test_moment_result_told_by_defaults_none():
    m = MomentResult.model_validate(_moment_row())
    assert m.told_by_user_id is None
    assert m.told_by_display_name is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/retrieval/test_speaker_first.py -q --tb=short -p no:warnings`
Expected: FAIL — `told_by_user_id` is not a field (or, depending on pydantic, the attribute is missing).

- [ ] **Step 3: Add the fields**

In `src/flashback/retrieval/schema.py`, `MomentResult` becomes:

```python
class MomentResult(BaseModel):
    id: UUID
    person_id: UUID
    title: str
    narrative: str
    time_anchor: dict | None
    life_period_estimate: str | None
    sensory_details: str | None
    emotional_tone: str | None
    contributor_perspective: str | None
    created_at: datetime
    similarity_score: float | None = None
    told_by_user_id: UUID | None = None
    told_by_display_name: str | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/retrieval/test_speaker_first.py -q --tb=short -p no:warnings`
Expected: PASS (2 tests).

- [ ] **Step 5: Verify (no commit)**

Run: `git diff --stat -- src/flashback/retrieval/schema.py tests/retrieval/test_speaker_first.py`
Confirm only those two files changed. Do NOT commit.

---

### Task 2: Speaker-first SQL + service binding

**Files:**
- Modify: `src/flashback/retrieval/queries.py:3-23` (`SEARCH_MOMENTS_SQL`)
- Modify: `src/flashback/retrieval/service.py:61-83` (`search_moments`) + add `SPEAKER_BIAS` constant
- Test: `tests/retrieval/test_speaker_first.py` (extend; DB-gated test added)

The current `SEARCH_MOMENTS_SQL` selects moment columns + `similarity_score` and orders by `narrative_embedding <=> query_vector`. We add the two provenance columns and bias the ORDER BY toward the current speaker.

- [ ] **Step 1: Write the failing DB-gated test**

Append to `tests/retrieval/test_speaker_first.py`:

```python
import os

import pytest


_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
def test_own_moment_outranks_equal_distance_other(db_pool):
    """With equal-ish embeddings, the current speaker's own moment ranks first."""
    # This test documents the contract; implementers wire it against the
    # db_pool fixture + a real RetrievalService. Build two active moments
    # for one person with near-identical narrative_embeddings — one
    # told_by user A, one told_by user B — then search with
    # current_user_id=A and assert A's moment is index 0.
    pytest.skip("contract test — implement against RetrievalService in this task")
```

> Implementer note: the existing retrieval DB tests (see `tests/retrieval/`)
> show the harness for seeding moments + embeddings against `db_pool`.
> Replace the skip with a real assertion: seed moment_A (told_by=A) and
> moment_B (told_by=B) with embeddings at the *same* cosine distance from a
> query vector, search with `current_user_id=A`, assert `results[0]` is
> moment_A. Add a second case: moment_B much closer than moment_A → B still
> wins (proves the bias is soft, not a hard tier).

- [ ] **Step 2: Run to verify it fails / skips appropriately**

Run: `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test` then
`.venv/Scripts/python.exe -m pytest tests/retrieval/test_speaker_first.py -q --tb=short -p no:warnings`
Expected: the new test errors/fails because `search_moments` does not yet
accept `current_user_id` (or skips if you left the skip in). Land the real
assertion before Step 4.

- [ ] **Step 3: Implement the SQL**

In `src/flashback/retrieval/queries.py`, replace `SEARCH_MOMENTS_SQL` with:

```python
SEARCH_MOMENTS_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT
        id, person_id, title, narrative, time_anchor,
        life_period_estimate, sensory_details, emotional_tone,
        contributor_perspective, created_at, narrative_embedding,
        told_by_user_id, told_by_display_name
    FROM   active_moments
    WHERE  person_id              = %(person_id)s
      AND  embedding_model         = %(embedding_model)s
      AND  embedding_model_version = %(embedding_model_version)s
      AND  narrative_embedding IS NOT NULL
)
SELECT
    id, person_id, title, narrative, time_anchor,
    life_period_estimate, sensory_details, emotional_tone,
    contributor_perspective, created_at,
    told_by_user_id, told_by_display_name,
    (narrative_embedding <=> %(query_vector)s) AS similarity_score
FROM   candidates
ORDER  BY (narrative_embedding <=> %(query_vector)s)
          - CASE WHEN told_by_user_id = %(current_user_id)s
                 THEN %(speaker_bias)s ELSE 0 END
LIMIT  %(limit)s
"""
```

Notes: `similarity_score` stays the **raw** distance (for display); the
ORDER BY subtracts `speaker_bias` from own-contributor distances so they
sort earlier. When `current_user_id` is NULL the CASE never matches → a
global no-op.

- [ ] **Step 4: Implement the service**

In `src/flashback/retrieval/service.py`, add near the top (module level,
after imports):

```python
# Soft speaker-first bias (spec D1): subtracted from the cosine distance of
# the current contributor's own moments so they rank slightly higher, while
# a much closer cross-contributor match can still win. Cosine distance is
# 0..2; 0.1 is a gentle nudge. Tunable.
SPEAKER_BIAS = 0.1
```

and change `search_moments`:

```python
    async def search_moments(
        self,
        query: str,
        person_id: UUID,
        limit: int | None = None,
        current_user_id: UUID | None = None,
    ) -> list[MomentResult]:
        """Vector similarity search over active moments for a person.

        ``current_user_id`` (the current speaker) biases ranking toward
        that contributor's own moments (soft, spec D1). None disables the
        bias (single-contributor / unknown speaker → no-op).
        """
        clamped_limit = self._clamp_limit(limit)
        vector = await self.embed_query(query)
        if vector is None:
            return []

        rows = await self._fetch_all(
            SEARCH_MOMENTS_SQL,
            {
                "person_id": person_id,
                "query_vector": Vector(vector),
                "embedding_model": self._embedding_model,
                "embedding_model_version": self._embedding_model_version,
                "limit": clamped_limit,
                "current_user_id": current_user_id,
                "speaker_bias": SPEAKER_BIAS,
            },
        )
        return [MomentResult.model_validate(row) for row in rows]
```

- [ ] **Step 5: Run the retrieval suite**

Run (with `TEST_DATABASE_URL` exported): `.venv/Scripts/python.exe -m pytest tests/retrieval -q --tb=short -p no:warnings`
Expected: new speaker-first tests PASS; pre-existing `test_voyage.py::test_happy_path_returns_vector` still fails (baseline); no other new failures.

- [ ] **Step 6: Verify (no commit)**

Run: `git diff --stat -- src/flashback/retrieval/queries.py src/flashback/retrieval/service.py tests/retrieval/test_speaker_first.py`

---

### Task 3: Orchestrator passes the current speaker into search

**Files:**
- Modify: `src/flashback/orchestrator/steps/retrieve.py:38-48` (recall branch)
- Test: `tests/orchestrator/` retrieval-step test (extend or add a focused unit)

- [ ] **Step 1: Write the failing test**

Find the existing retrieve-step test (`grep -rln "search_moments" tests/orchestrator`). If one drives `retrieve()` with a fake retrieval service capturing call kwargs, extend it; otherwise add `tests/orchestrator/test_retrieve_speaker.py`:

```python
"""retrieve() forwards the current speaker's user_id to search_moments."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.orchestrator.state import TurnState
from flashback.orchestrator.steps.retrieve import retrieve


class _FakeRetrieval:
    def __init__(self):
        self.search_moments_kwargs = None

    async def search_moments(self, **kwargs):
        self.search_moments_kwargs = kwargs
        return []

    async def search_entities(self, **kwargs):
        return []


class _Deps:
    def __init__(self, retrieval):
        self.retrieval = retrieval


def _state(user_id):
    return TurnState(
        turn_id=uuid4(),
        session_id=uuid4(),
        person_id=uuid4(),
        user_id=user_id,
        user_message="tell me about the halwa",
        started_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_recall_forwards_current_user_id():
    retr = _FakeRetrieval()
    state = _state(uuid4())
    state.effective_intent = "recall"
    await retrieve(state, _Deps(retr))
    assert retr.search_moments_kwargs["current_user_id"] == state.user_id
```

> Implementer note: confirm `TurnState`'s required fields against
> `src/flashback/orchestrator/state.py` and match them exactly (the dataclass
> ordering changed in sub-project 1: `user_id` is optional with default None).
> If the existing orchestrator retrieve test uses a different fake/deps shape,
> mirror that instead of this scaffold.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_retrieve_speaker.py -q --tb=short -p no:warnings`
Expected: FAIL — `search_moments` is currently called with `query`/`person_id` only, so `current_user_id` is absent from kwargs (KeyError).

- [ ] **Step 3: Implement**

In `src/flashback/orchestrator/steps/retrieve.py`, the `recall` branch's
`search_moments` call gains the speaker:

```python
            if intent == "recall":
                state.related_moments, state.related_entities = await asyncio.gather(
                    deps.retrieval.search_moments(
                        query=state.user_message,
                        person_id=state.person_id,
                        current_user_id=state.user_id,
                    ),
                    deps.retrieval.search_entities(
                        query=state.user_message,
                        person_id=state.person_id,
                    ),
                )
```

(`search_entities` is unchanged — entities get no speaker bias, spec D1.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_retrieve_speaker.py tests/orchestrator -q --tb=short -p no:warnings`
Expected: new test PASSES; orchestrator baseline failures unchanged (the 5 pre-existing in that dir), no new ones.

- [ ] **Step 5: Verify (no commit)**

Run: `git diff --stat -- src/flashback/orchestrator/steps/retrieve.py tests/orchestrator/`

---

### Task 4: `TurnContext.current_user_id`

**Files:**
- Modify: `src/flashback/response_generator/schema.py:74-103` (`TurnContext`)
- Test: `tests/response_generator/test_attribution_render.py` (create — model field check)

- [ ] **Step 1: Write the failing test**

Create `tests/response_generator/test_attribution_render.py`:

```python
"""Cross-contributor attribution rendering (sub-project 2)."""

from datetime import datetime, timezone
from uuid import uuid4

from flashback.response_generator.schema import TurnContext


def test_turn_context_accepts_current_user_id():
    uid = uuid4()
    ctx = TurnContext(
        person_name="Lakshmi",
        intent="recall",
        emotional_temperature="medium",
        current_user_id=uid,
    )
    assert ctx.current_user_id == uid


def test_turn_context_current_user_id_defaults_none():
    ctx = TurnContext(
        person_name="Lakshmi",
        intent="recall",
        emotional_temperature="medium",
    )
    assert ctx.current_user_id is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: FAIL — `current_user_id` not a field (`extra="forbid"` raises on the first test).

- [ ] **Step 3: Implement**

In `src/flashback/response_generator/schema.py`, add to `TurnContext`
(after `person_gender`, before `intent`, so it sits with identity fields;
import `UUID` at top: `from uuid import UUID`):

```python
    # Current speaker (spec sub-project 2). Used by render_turn_context to
    # decide which retrieved moments belong to *other* contributors and
    # must be credited. None = unknown/single-contributor → no attribution.
    current_user_id: UUID | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: PASS (2 tests).

- [ ] **Step 5: Verify (no commit)**

Run: `git diff --stat -- src/flashback/response_generator/schema.py tests/response_generator/test_attribution_render.py`

---

### Task 5: Attribution in `render_turn_context`

**Files:**
- Modify: `src/flashback/response_generator/context.py:34-43` (the `<moments>` block in `render_turn_context`)
- Test: `tests/response_generator/test_attribution_render.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/response_generator/test_attribution_render.py`:

```python
from flashback.response_generator.context import render_turn_context
from flashback.retrieval.schema import MomentResult


def _moment(told_by_user_id=None, told_by_display_name=None, title="Halwa lessons"):
    return MomentResult(
        id=uuid4(),
        person_id=uuid4(),
        title=title,
        narrative="She taught me to make halwa.",
        time_anchor=None,
        life_period_estimate=None,
        sensory_details=None,
        emotional_tone=None,
        contributor_perspective=None,
        created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        told_by_user_id=told_by_user_id,
        told_by_display_name=told_by_display_name,
    )


def _ctx(current_user_id, moments):
    return TurnContext(
        person_name="Lakshmi",
        intent="recall",
        emotional_temperature="medium",
        current_user_id=current_user_id,
        related_moments=moments,
    )


def test_other_contributor_moment_is_attributed():
    me = uuid4()
    other = uuid4()
    rendered = render_turn_context(
        _ctx(me, [_moment(told_by_user_id=other, told_by_display_name="Ravi")])
    )
    assert 'told_by="Ravi"' in rendered


def test_own_moment_not_attributed():
    me = uuid4()
    rendered = render_turn_context(
        _ctx(me, [_moment(told_by_user_id=me, told_by_display_name="Priya")])
    )
    assert "told_by=" not in rendered


def test_null_provenance_moment_not_attributed():
    me = uuid4()
    rendered = render_turn_context(_ctx(me, [_moment()]))
    assert "told_by=" not in rendered


def test_no_current_user_no_attribution():
    other = uuid4()
    rendered = render_turn_context(
        _ctx(None, [_moment(told_by_user_id=other, told_by_display_name="Ravi")])
    )
    assert "told_by=" not in rendered
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: `test_other_contributor_moment_is_attributed` FAILS (no `told_by` label rendered yet); the negative tests pass trivially.

- [ ] **Step 3: Implement**

In `src/flashback/response_generator/context.py`, replace the moments block
(currently lines ~34-43) in `render_turn_context`:

```python
    if ctx.related_moments:
        lines = []
        for moment in ctx.related_moments:
            similarity = ""
            if moment.similarity_score is not None:
                similarity = f"  (similarity: {moment.similarity_score:.2f})"
            attribution = ""
            if (
                ctx.current_user_id is not None
                and moment.told_by_user_id is not None
                and moment.told_by_user_id != ctx.current_user_id
                and moment.told_by_display_name
            ):
                attribution = f' told_by="{xml_text(moment.told_by_display_name)}"'
            lines.append(
                f"- {xml_text(moment.title)}: {xml_text(moment.narrative)}"
                f"{attribution}{similarity}"
            )
        retrieval_sections.append(_block("moments", "\n".join(lines)))
```

(The `told_by` label sits on the moment line; only cross-contributor moments
with a known display name get it — spec D3.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: PASS (6 tests total in the file).

- [ ] **Step 5: Run the full response_generator suite**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator -q --tb=short -p no:warnings`
Expected: no new failures vs baseline (response_generator has no pre-existing baseline failures).

- [ ] **Step 6: Verify (no commit)**

Run: `git diff --stat -- src/flashback/response_generator/context.py tests/response_generator/test_attribution_render.py`

---

### Task 6: Crediting instruction in the recall prompt

**Files:**
- Modify: `src/flashback/response_generator/prompts.py:166-177` (`RECALL_PROMPT`)
- Test: `tests/response_generator/` prompt presence test (add a small assertion;
  check for an existing prompt-content test file first with
  `grep -rln "RECALL_PROMPT\|PROMPTS" tests/response_generator`)

- [ ] **Step 1: Write the failing test**

Add to `tests/response_generator/test_attribution_render.py` (keeps the
sub-project's tests together):

```python
from flashback.response_generator.prompts import RECALL_PROMPT


def test_recall_prompt_has_attribution_instruction():
    p = RECALL_PROMPT.lower()
    assert "told_by" in p
    # must instruct crediting + never claiming as the speaker's own
    assert "credit" in p or "attribut" in p
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py::test_recall_prompt_has_attribution_instruction -q --tb=short -p no:warnings`
Expected: FAIL — `RECALL_PROMPT` has no `told_by` / crediting language.

- [ ] **Step 3: Implement**

In `src/flashback/response_generator/prompts.py`, extend `RECALL_PROMPT`:

```python
RECALL_PROMPT = BASE_SYSTEM_PROMPT + """

INTENT: recall

The contributor is referencing something from earlier in the
conversation or bringing up a memory or fact about the subject. You
have retrieval results below. Use them to anchor your response - show
that you remember what they shared, then gently invite them to expand
on it.

Reference a specific detail from the retrieved context.

ATTRIBUTION: A retrieved moment may carry a told_by="Name" label, which
means a DIFFERENT contributor shared that memory — not the person you
are speaking with now. When you draw on such a moment, credit them
naturally by name ("Ravi has told us about...") and invite the current
contributor to add their own perspective. Never present another
contributor's memory as if this person told you. Moments with no
told_by label are this contributor's own (or shared history); use them
without crediting anyone.
"""
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: PASS (7 tests).

- [ ] **Step 5: Verify (no commit)**

Run: `git diff --stat -- src/flashback/response_generator/prompts.py tests/response_generator/test_attribution_render.py`

---

### Task 7: Wire `current_user_id` into `TurnContext`

**Files:**
- Modify: `src/flashback/orchestrator/steps/generate_response.py:40-77` (`build_turn_context`)
- Test: `tests/orchestrator/` build-context test (extend or add)

- [ ] **Step 1: Write the failing test**

Add `tests/orchestrator/test_build_turn_context_speaker.py`:

```python
"""build_turn_context carries the current speaker's user_id (sub-project 2)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.orchestrator.state import TurnState
from flashback.orchestrator.steps.generate_response import build_turn_context


@pytest.mark.asyncio
async def test_build_turn_context_sets_current_user_id(monkeypatch):
    user_id = uuid4()
    # See the existing generate_response tests for the deps/person/WM fakes;
    # mirror them. The assertion: the returned TurnContext.current_user_id
    # equals state.user_id.
    pytest.skip("wire against the existing build_turn_context test harness")
```

> Implementer note: `build_turn_context` calls `fetch_person`, reads
> `working_memory_state`, and `get_transcript`. Find the existing test that
> already stands these up (`grep -rln "build_turn_context" tests/`), copy its
> fakes, and replace the skip with: set `state.user_id = user_id`, call
> `build_turn_context`, assert `ctx.current_user_id == user_id`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_build_turn_context_speaker.py -q --tb=short -p no:warnings`
Expected: FAIL once the skip is replaced (TurnContext.current_user_id is None because build_turn_context doesn't set it).

- [ ] **Step 3: Implement**

In `src/flashback/orchestrator/steps/generate_response.py`, add to the
`TurnContext(...)` constructor in `build_turn_context` (alongside the other
fields):

```python
        current_user_id=state.user_id,
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_build_turn_context_speaker.py tests/orchestrator -q --tb=short -p no:warnings`
Expected: new test PASSES; orchestrator baseline (5 pre-existing) unchanged.

- [ ] **Step 5: Verify (no commit)**

Run: `git diff --stat -- src/flashback/orchestrator/steps/generate_response.py tests/orchestrator/`

---

### Task 8: End-to-end no-op guard + final verification sweep

**Files:**
- Test: `tests/response_generator/test_attribution_render.py` (add the no-op case)

- [ ] **Step 1: Add the single-contributor no-op test**

Append to `tests/response_generator/test_attribution_render.py`:

```python
def test_single_contributor_render_is_unattributed():
    """All moments own-or-null + a known speaker → zero attribution labels.

    This is the safety guarantee that the feature is invisible until real
    multi-contributor data exists (spec D4).
    """
    me = uuid4()
    moments = [
        _moment(told_by_user_id=me, told_by_display_name="Priya", title="A"),
        _moment(told_by_user_id=None, title="B"),  # creator-era
    ]
    rendered = render_turn_context(_ctx(me, moments))
    assert "told_by=" not in rendered
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q --tb=short -p no:warnings`
Expected: PASS (8 tests).

- [ ] **Step 3: Full-suite sweep (no DB)**

Run: `.venv/Scripts/python.exe -m pytest -q --tb=no -p no:warnings 2>&1 | grep -cE "^FAILED"`
Expected: `14` (the pre-existing baseline; zero new failures).

- [ ] **Step 4: Full-suite sweep (with DB)**

Run: `export TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback_test` then
`.venv/Scripts/python.exe -m pytest -q --tb=no -p no:warnings 2>&1 | tail -3`
Expected: the speaker-first DB test passes; failures stay at the
environment baseline (some of the 14 are DB-independent and remain). Record
the delta — there must be no NEW failures attributable to this sub-project.

- [ ] **Step 5: Provenance read-path audit**

Run: `grep -rn "current_user_id\|told_by" src/flashback/retrieval src/flashback/response_generator src/flashback/orchestrator/steps/retrieve.py src/flashback/orchestrator/steps/generate_response.py`
Expected: references appear in exactly the files this plan modified
(queries.py, service.py, schema.py x2, context.py, prompts.py, retrieve.py,
generate_response.py). No other module reads provenance.

- [ ] **Step 6: Report (no commit)**

Run: `git status --short` and `git diff --stat`
Summarize the full working-tree change set for the user to commit/push.
Do NOT commit.

---

## Self-review (author checklist — completed)

**Spec coverage:**
- §2.6 soft bias → Tasks 2 (SQL/service) + 3 (orchestrator wiring). ✓
- §2.5 attribution → Tasks 4 (context field) + 5 (renderer) + 6 (prompt) + 7 (wiring). ✓
- D1 soft additive, moments only, SPEAKER_BIAS=0.1 → Task 2. ✓
- D2 own=exact match, NULL neutral → Tasks 2 (CASE) + 5 (render condition). ✓
- D3 name-only cross-contributor attribution → Tasks 5 + 6. ✓
- D4 single-contributor no-op → Task 8 guard test. ✓
- "No migration" → none in plan. ✓
- MomentResult provenance prerequisite → Task 1. ✓

**Type consistency:** `current_user_id` is `UUID | None` everywhere
(MomentResult.told_by_user_id, TurnContext.current_user_id, search_moments
param, TurnState.user_id). `told_by_display_name` is `str | None`.
`SPEAKER_BIAS` float constant used only in service.py + SQL param.

**No placeholders:** the two "contract test" skips in Tasks 2 and 7 are
deliberately handed to the implementer with explicit instructions to wire
against the existing DB / build-context harness (those harnesses' exact fake
shapes aren't reproduced here to avoid guessing them wrong); every other step
has complete code.
