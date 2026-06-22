# Collaborator Question Scoping (SP4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **NO COMMITS THIS CYCLE.** Standing user constraint: all work lands in the
> working tree on `feature/collaborator-provenance`; the user commits/pushes to
> their dev branch. **Skip every `git add` / `git commit` step.** Each task ends
> with a working-tree verification step instead of a commit.

**Goal:** A contributor's session only surfaces questions tied to their own contributions or to shared/global content — never another collaborator's personal/private content — via a deterministic `told_by_user_id` provenance filter combined with an LLM-emitted `scope` (public/personal/private) sensitivity tier.

**Architecture:** Each question-generating LLM emits `attributes.scope`. Code stamps `told_by_user_id` on every question (already done for per-session producers + P1; newly derived for P4 thread_deepen). The steady/starter question selector gains a `current_user_id` parameter and a single combined SQL eligibility clause. Scope lives in the existing `attributes` JSONB — no schema migration for it.

**Tech Stack:** Python, psycopg (async pool for selection, sync for workers), Pydantic, Postgres + JSONB, pytest (`asyncio_mode=auto`).

**Test command (no-DB):** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
**Test command (DB-gated):** same, with `TEST_DATABASE_URL` set to a live Postgres. DB-gated tests skip when it is unset.
**Baselines on this machine:** 14 no-DB failures, 28 with-DB failures (pre-existing). Judge regressions by diffing the FAILED list, not absolute count.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/flashback/questions/scope.py` (new) | Scope tier constants + `normalize_scope()` write-time coercion | 1 |
| `src/flashback/phase_gate/queries.py` | Combined eligibility SQL clause + `current_user_id` param | 2, 3 |
| `src/flashback/phase_gate/steady_selector.py` | Thread `current_user_id` → SQL params | 2 |
| `src/flashback/phase_gate/gate.py` | Thread `current_user_id` through `select_next_question` | 2 |
| `src/flashback/orchestrator/steps/select_question.py` | Pass `state.user_id` (steady `/turn`) | 2 |
| `src/flashback/orchestrator/steps/starter_opener.py` | Pass `state.user_id` (`select_starter_question`) | 2 |
| `src/flashback/orchestrator/steps/select_coverage_tap.py` | Pass `current_user_id` to coverage-tap queries | 3 |
| `src/flashback/workers/producers/schema.py` | `GeneratedQuestion.scope` | 4 |
| `src/flashback/workers/producers/prompts.py` | `scope` in P2/P3/P5 tool schemas + rubric in system prompts | 4 |
| `src/flashback/workers/producers/{underdeveloped,life_period,universal}.py` | Map `scope` into `GeneratedQuestion` | 4 |
| `src/flashback/workers/producers/persistence.py` | Write `attributes["scope"]` | 4 |
| `src/flashback/workers/thread_detector/schema.py` | `P4Question.scope` | 5 |
| `src/flashback/workers/thread_detector/prompts.py` | `scope` in P4 tool + rubric | 5 |
| `src/flashback/workers/thread_detector/persistence.py` | Derive `told_by_user_id` from members; write scope | 5 |
| `src/flashback/workers/extraction/schema.py` | `DroppedReference.scope` | 6 |
| `src/flashback/workers/extraction/prompts.py` | `scope` in dropped_references tool + rubric | 6 |
| `src/flashback/workers/extraction/persistence.py` | Write `attributes["scope"]` | 6 |
| `migrations/0030_collaborator_onboarding_display_name.{up,down}.sql` (new) | `display_name` column | 7 |
| `src/flashback/collaborator_onboarding/{queries,repository}.py` | Mirror `display_name` | 7 |
| `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py` | Pass `contributor_display_name` | 7 |
| `migrations/0031_coverage_tap_scope_public.{up,down}.sql` (new) | Seed `scope='public'` on coverage taps | 8 |
| `CLAUDE.md` | Invariant #26 update + new scope invariant | 9 |

**Build order rationale:** Task 1 (helper) → Task 2 (the actual leak fix; testable immediately against existing stamped questions) → Task 3 (coverage-tap consistency) → Tasks 4–6 (scope emission per LLM surface) → Tasks 7–8 (migrations/riders) → Task 9 (docs). Tasks 2 fixes both observed leaks on its own, because the leaking questions (`underdeveloped_entity`, `dropped_reference`) are already `told_by`-stamped and default-`personal`.

---

## Task 1: Scope tier helper

**Files:**
- Create: `src/flashback/questions/__init__.py`
- Create: `src/flashback/questions/scope.py`
- Test: `tests/questions/test_scope.py`

- [ ] **Step 1: Write the failing test**

Create `tests/questions/__init__.py` (empty) and `tests/questions/test_scope.py`:

```python
from flashback.questions.scope import (
    DEFAULT_SCOPE,
    PERSONAL,
    PRIVATE,
    PUBLIC,
    VALID_SCOPES,
    normalize_scope,
)


def test_valid_scopes_are_the_three_tiers():
    assert VALID_SCOPES == frozenset({PUBLIC, PERSONAL, PRIVATE})
    assert DEFAULT_SCOPE == PERSONAL


def test_normalize_keeps_valid_values_case_insensitively():
    assert normalize_scope("public") == "public"
    assert normalize_scope(" Private ") == "private"
    assert normalize_scope("PERSONAL") == "personal"


def test_normalize_defaults_to_personal_for_missing_or_unknown():
    assert normalize_scope(None) == "personal"
    assert normalize_scope("") == "personal"
    assert normalize_scope("secret") == "personal"
    assert normalize_scope(123) == "personal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/questions/test_scope.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashback.questions'`

- [ ] **Step 3: Write the implementation**

Create `src/flashback/questions/__init__.py` (empty file).

Create `src/flashback/questions/scope.py`:

```python
"""Question scope tiers for collaborator content-scoping (SP4).

The producer LLM labels a question; the selection SQL enforces who may
be asked it. ``normalize_scope`` is the write-time coercion so every
persisted row is self-describing; the selection SQL independently
fail-safes any non-public/non-personal label to teller-only.
"""

from __future__ import annotations

PUBLIC = "public"
PERSONAL = "personal"
PRIVATE = "private"

VALID_SCOPES = frozenset({PUBLIC, PERSONAL, PRIVATE})
DEFAULT_SCOPE = PERSONAL


def normalize_scope(value: object) -> str:
    """Coerce an LLM- or user-supplied scope to a valid tier.

    Missing / empty / unknown / non-string → ``DEFAULT_SCOPE``
    (``'personal'``), the safe provenance-gated tier.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in VALID_SCOPES:
            return candidate
    return DEFAULT_SCOPE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/questions/test_scope.py -q -p no:warnings`
Expected: PASS (4 passed)

- [ ] **Step 5: Verify working tree**

Run: `git status --short`
Expected: `src/flashback/questions/` and `tests/questions/` shown as untracked. **Do not commit.**

---

## Task 2: Combined eligibility filter + `current_user_id` wiring (the leak fix)

**Files:**
- Modify: `src/flashback/phase_gate/queries.py` (`SELECT_STEADY_CANDIDATES`)
- Modify: `src/flashback/phase_gate/steady_selector.py` (`select`, `_fetch_candidates`)
- Modify: `src/flashback/phase_gate/gate.py` (`select_next_question`)
- Modify: `src/flashback/orchestrator/steps/select_question.py`
- Modify: `src/flashback/orchestrator/steps/starter_opener.py` (`select_starter_question`)
- Test: `tests/phase_gate/test_scope_provenance.py` (new)
- Test: `tests/phase_gate/test_steady_selector.py` (existing — signature update)

- [ ] **Step 1: Write the failing DB-gated test**

Create `tests/phase_gate/test_scope_provenance.py`. This reproduces both observed leaks and the tier matrix. It uses the existing DB fixtures pattern (see `tests/phase_gate/test_queries.py` for the conftest fixture names — reuse `db_pool` / person-insert helpers there; if a helper to insert a question with `told_by_user_id` + `attributes` does not exist, insert inline with raw SQL as below).

```python
import uuid

import pytest

from flashback.phase_gate.queries import SELECT_STEADY_CANDIDATES

pytestmark = pytest.mark.asyncio


async def _insert_question(cur, *, person_id, source, scope, told_by):
    qid = uuid.uuid4()
    await cur.execute(
        """
        INSERT INTO questions (id, person_id, text, source, attributes, told_by_user_id)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
        """,
        (str(qid), str(person_id), f"q-{scope}-{source}", source,
         f'{{"scope": "{scope}", "themes": ["family"]}}' if scope else '{"themes": ["family"]}',
         str(told_by) if told_by else None),
    )
    return qid


async def _candidate_ids(cur, *, person_id, current_user_id):
    await cur.execute(
        SELECT_STEADY_CANDIDATES,
        {
            "person_id": str(person_id),
            "recent_ids": [],
            "sources": ["underdeveloped_entity", "dropped_reference",
                        "thread_deepen", "life_period_gap", "universal_dimension"],
            "exclude_skipped": True,
            "current_user_id": str(current_user_id) if current_user_id else None,
        },
    )
    return {row[0] for row in await cur.fetchall()}


async def test_scope_provenance_matrix(db_conn, make_person):
    person_id = await make_person(db_conn)
    daughter = uuid.uuid4()
    son = None  # creator era: NULL told_by, NULL current_user_id

    async with db_conn.cursor() as cur:
        pub = await _insert_question(cur, person_id=person_id, source="universal_dimension", scope="public", told_by=daughter)
        pers_own = await _insert_question(cur, person_id=person_id, source="underdeveloped_entity", scope="personal", told_by=daughter)
        pers_null = await _insert_question(cur, person_id=person_id, source="dropped_reference", scope="personal", told_by=None)
        priv_own = await _insert_question(cur, person_id=person_id, source="thread_deepen", scope="private", told_by=daughter)
        untagged = await _insert_question(cur, person_id=person_id, source="life_period_gap", scope=None, told_by=daughter)

        # Daughter sees: public, her own personal, the NULL/shared personal,
        # her own private, her own untagged(=personal).
        seen_by_daughter = await _candidate_ids(cur, person_id=person_id, current_user_id=daughter)
        assert {pub, pers_own, pers_null, priv_own, untagged} <= seen_by_daughter

        # Son/creator (NULL) sees: public, the NULL/shared personal only.
        # NOT the daughter's personal, private, or untagged(=personal).
        seen_by_son = await _candidate_ids(cur, person_id=person_id, current_user_id=son)
        assert pub in seen_by_son
        assert pers_null in seen_by_son
        assert pers_own not in seen_by_son      # leak #1/#2 fix
        assert priv_own not in seen_by_son
        assert untagged not in seen_by_son
```

> If `db_conn` / `make_person` fixtures differ in this repo, mirror the exact fixture names used in `tests/phase_gate/test_queries.py` and `tests/retrieval/conftest.py`. Read those first.

- [ ] **Step 2: Run test to verify it fails**

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest tests/phase_gate/test_scope_provenance.py -q -p no:warnings`
Expected: FAIL — `SELECT_STEADY_CANDIDATES` does not accept `current_user_id` (psycopg raises on the unused/missing `%(current_user_id)s` param, or the assertions fail because no scope filter exists). If `TEST_DATABASE_URL` is unset, the test SKIPS — that is not a pass; set it.

- [ ] **Step 3: Add the eligibility clause to `SELECT_STEADY_CANDIDATES`**

In `src/flashback/phase_gate/queries.py`, add this clause to the `WHERE` of `SELECT_STEADY_CANDIDATES`, immediately after the `AND (d.action IS NULL OR d.action != 'suppress')` line (before the suppress-sibling `NOT EXISTS` blocks):

```sql
  -- Collaborator content-scoping (SP4). A contributor only sees questions
  -- that are public, their own (told_by = them) + shared (told_by NULL) when
  -- personal, or strictly theirs when private. Untagged rows default to
  -- 'personal'. NULL current_user_id is the creator era and matches only
  -- NULL-told_by rows. See CLAUDE.md scope invariant.
  AND (
        COALESCE(q.attributes->>'scope', 'personal') = 'public'
     OR (COALESCE(q.attributes->>'scope', 'personal') = 'personal'
           AND (q.told_by_user_id IS NULL
                OR q.told_by_user_id = %(current_user_id)s))
     OR (COALESCE(q.attributes->>'scope', 'personal') NOT IN ('public', 'personal')
           AND q.told_by_user_id IS NOT DISTINCT FROM %(current_user_id)s)
      )
```

- [ ] **Step 4: Thread `current_user_id` through the selector**

In `src/flashback/phase_gate/steady_selector.py`:

Change `select` signature (add `current_user_id`):

```python
    async def select(
        self,
        person_id: UUID,
        session_id: UUID,
        *,
        sources: tuple[str, ...] = STEADY_SOURCES,
        active_theme_slug: str | None = None,
        last_seeded_source: str | None = None,
        current_user_id: UUID | None = None,
    ) -> SelectionResult:
```

Pass `current_user_id` into each `_fetch_candidates` call (there are three — the initial call and the two fallbacks). Update `_fetch_candidates`:

```python
    async def _fetch_candidates(
        self,
        person_id: UUID,
        recent_ids: list[UUID],
        sources: tuple[str, ...],
        *,
        exclude_skipped: bool,
        current_user_id: UUID | None = None,
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
                        "current_user_id": current_user_id,
                    },
                )
                rows = await cur.fetchall()
        return [ ... ]  # unchanged
```

Update the three call sites inside `select` to pass `current_user_id=current_user_id`:

```python
        candidates = await self._fetch_candidates(
            person_id, recent_ids, effective_sources,
            exclude_skipped=True, current_user_id=current_user_id,
        )
        if not candidates and effective_sources != sources:
            candidates = await self._fetch_candidates(
                person_id, recent_ids, sources,
                exclude_skipped=True, current_user_id=current_user_id,
            )
            used_source_cooldown_fallback = bool(candidates)
        if not candidates:
            candidates = await self._fetch_candidates(
                person_id, recent_ids, sources,
                exclude_skipped=False, current_user_id=current_user_id,
            )
            used_skip_fallback = bool(candidates)
```

- [ ] **Step 5: Thread `current_user_id` through `select_next_question`**

In `src/flashback/phase_gate/gate.py`, add `current_user_id` to `select_next_question` and pass it to both `_steady.select` calls:

```python
    async def select_next_question(
        self,
        person_id: UUID,
        session_id: UUID,
        recently_asked_ids: list[UUID] | None = None,
        active_theme_slug: str | None = None,
        last_seeded_source: str | None = None,
        current_user_id: UUID | None = None,
    ) -> SelectionResult:
        ...
        if phase == "starter":
            result = await self._steady.select(
                person_id,
                session_id,
                sources=STARTER_FALLBACK_SOURCES,
                active_theme_slug=active_theme_slug,
                last_seeded_source=last_seeded_source,
                current_user_id=current_user_id,
            )
        else:
            result = await self._steady.select(
                person_id,
                session_id,
                active_theme_slug=active_theme_slug,
                last_seeded_source=last_seeded_source,
                current_user_id=current_user_id,
            )
```

- [ ] **Step 6: Pass `state.user_id` from the two orchestrator call sites**

In `src/flashback/orchestrator/steps/select_question.py`, add to the `select_next_question` call (line ~45):

```python
        state.selection = await deps.phase_gate.select_next_question(
            person_id=state.person_id,
            session_id=state.session_id,
            recently_asked_ids=recently_asked_ids,
            active_theme_slug=active_theme_slug,
            last_seeded_source=last_seeded_source,
            current_user_id=state.user_id,
        )
```

In `src/flashback/orchestrator/steps/starter_opener.py`, `select_starter_question` (line ~123):

```python
            state.selection = await phase_gate.select_next_question(
                person_id=state.person_id,
                session_id=state.session_id,
                recently_asked_ids=[],
                active_theme_slug=None,
                last_seeded_source=None,
                current_user_id=state.user_id,
            )
```

- [ ] **Step 7: Update the existing steady-selector test's fakes/signatures**

In `tests/phase_gate/test_steady_selector.py`, any direct `SteadySelector.select(...)` call now accepts the new keyword; existing calls remain valid (it defaults to `None`). If the test asserts exact SQL params, add `"current_user_id": None` to the expected dict. In `tests/orchestrator/test_orchestrator_with_phase_gate.py`, update `FakePhaseGate.select_next_question` to accept the new kwarg:

```python
    async def select_next_question(self, person_id, session_id,
                                   recently_asked_ids=None, active_theme_slug=None,
                                   last_seeded_source=None, current_user_id=None):
```

- [ ] **Step 8: Run the new + affected tests**

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest tests/phase_gate/test_scope_provenance.py tests/phase_gate/test_steady_selector.py tests/orchestrator/test_orchestrator_with_phase_gate.py -q -p no:warnings`
Expected: PASS (the matrix test green; no regressions in the two existing files).

- [ ] **Step 9: Full no-DB regression**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
Expected: 14 failures (baseline), none newly introduced. Diff the FAILED list against baseline.

- [ ] **Step 10: Verify working tree** (`git status --short`; do not commit)

---

## Task 3: Coverage-tap selector consistency

**Files:**
- Modify: `src/flashback/phase_gate/queries.py` (`SELECT_UNANSWERED_COVERAGE_TAP`, `SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION`)
- Modify: `src/flashback/orchestrator/steps/select_coverage_tap.py`
- Test: `tests/phase_gate/test_scope_provenance.py` (extend)

> Coverage taps are `person_id IS NULL` global templates with `told_by_user_id` NULL and (after Task 8) `scope='public'`, so the filter is effectively a no-op for them today. We add it for spec fidelity (D6) and to future-proof person-scoped taps. Both queries alias the table `q`, so the clause drops in unchanged.

- [ ] **Step 1: Write the failing test (extend matrix file)**

Add to `tests/phase_gate/test_scope_provenance.py`:

```python
async def test_coverage_tap_query_accepts_current_user_id(db_conn):
    from flashback.phase_gate.queries import SELECT_UNANSWERED_COVERAGE_TAP
    async with db_conn.cursor() as cur:
        # Must not raise on the new param.
        await cur.execute(
            SELECT_UNANSWERED_COVERAGE_TAP,
            {"dimension": "era", "recent_ids": [],
             "person_id": str(uuid.uuid4()), "current_user_id": None},
        )
        await cur.fetchall()
```

- [ ] **Step 2: Run to verify it fails**

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest tests/phase_gate/test_scope_provenance.py::test_coverage_tap_query_accepts_current_user_id -q -p no:warnings`
Expected: FAIL — psycopg raises on unknown/missing `%(current_user_id)s` (the query has no such placeholder yet).

- [ ] **Step 3: Add the clause to both coverage-tap queries**

In `SELECT_UNANSWERED_COVERAGE_TAP` and `SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION`, add immediately before `ORDER BY random()`:

```sql
  AND (
        COALESCE(q.attributes->>'scope', 'personal') = 'public'
     OR (COALESCE(q.attributes->>'scope', 'personal') = 'personal'
           AND (q.told_by_user_id IS NULL
                OR q.told_by_user_id = %(current_user_id)s))
     OR (COALESCE(q.attributes->>'scope', 'personal') NOT IN ('public', 'personal')
           AND q.told_by_user_id IS NOT DISTINCT FROM %(current_user_id)s)
      )
```

- [ ] **Step 4: Pass `current_user_id` from the coverage-tap step**

In `src/flashback/orchestrator/steps/select_coverage_tap.py`, locate every `cur.execute(SELECT_UNANSWERED_COVERAGE_TAP, {...})` and `SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION` call and add `"current_user_id": state.user_id` to the params dict. (Read the file first to find the exact param-dict construction; there are two query executions.)

- [ ] **Step 5: Run the extended test**

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest tests/phase_gate/test_scope_provenance.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 6: Affected no-DB tests for the coverage-tap step**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest tests/orchestrator -q -p no:warnings`
Expected: no new failures vs baseline.

- [ ] **Step 7: Verify working tree** (`git status --short`; do not commit)

---

## Task 4: Scope emission — P2/P3/P5 producers

**Files:**
- Modify: `src/flashback/workers/producers/schema.py` (`GeneratedQuestion`)
- Modify: `src/flashback/workers/producers/prompts.py` (P2/P3/P5 tool schemas + system prompts)
- Modify: `src/flashback/workers/producers/underdeveloped.py` (attributes assembly ~line 243)
- Modify: `src/flashback/workers/producers/life_period.py` (attributes assembly ~line 107)
- Modify: `src/flashback/workers/producers/universal.py` (attributes assembly ~line 161)
- Modify: `src/flashback/workers/producers/persistence.py` (`persist_producer_result`)
- Test: `tests/workers/producers/test_scope.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/workers/producers/test_scope.py`:

```python
from flashback.workers.producers.persistence import _build_question_attributes
from flashback.workers.producers.schema import GeneratedQuestion


def test_generated_question_defaults_scope_personal():
    q = GeneratedQuestion(text="t", themes=["family"])
    assert q.scope == "personal"


def test_build_attributes_includes_normalized_scope_and_themes():
    q = GeneratedQuestion(text="t", themes=["family"],
                          attributes={"dimension": "era"}, scope="public")
    attrs = _build_question_attributes(q)
    assert attrs["scope"] == "public"
    assert attrs["themes"] == ["family"]
    assert attrs["dimension"] == "era"


def test_build_attributes_defaults_scope_personal_when_unset():
    attrs = _build_question_attributes(GeneratedQuestion(text="t", themes=["family"]))
    assert attrs["scope"] == "personal"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/workers/producers/test_scope.py -q -p no:warnings`
Expected: FAIL — `GeneratedQuestion` has no `scope`; `_build_question_attributes` does not exist.

- [ ] **Step 3: Add `scope` to `GeneratedQuestion`**

In `src/flashback/workers/producers/schema.py`, add the field and import:

```python
from typing import Literal
# ...
class GeneratedQuestion(BaseModel):
    """A single question produced by P2, P3, or P5."""

    model_config = ConfigDict(extra="forbid")

    text: str
    themes: list[str] = Field(min_length=1)
    attributes: dict = Field(default_factory=dict)
    targets_entity_id: UUID | None = None
    scope: Literal["public", "personal", "private"] = "personal"
```

- [ ] **Step 4: Extract a shared attributes builder in persistence**

In `src/flashback/workers/producers/persistence.py`, add a helper and use it in `persist_producer_result`. Replace the existing per-question attributes block:

```python
from flashback.questions.scope import normalize_scope


def _build_question_attributes(q) -> dict:
    """Compose the persisted attributes dict: caller attrs + themes + scope."""
    attributes = dict(q.attributes)
    attributes["themes"] = list(q.themes)
    attributes["scope"] = normalize_scope(getattr(q, "scope", None))
    return attributes
```

In `persist_producer_result`, replace:

```python
        attributes = dict(q.attributes)
        attributes["themes"] = list(q.themes)
```

with:

```python
        attributes = _build_question_attributes(q)
```

- [ ] **Step 5: Add `scope` to the P2/P3/P5 tool schemas + system prompts**

In `src/flashback/workers/producers/prompts.py`, add this property to the per-question `items.properties` of `P2_TOOL`, `P3_TOOL`, and `P5_TOOL`, and add `"scope"` to each `required` list:

```python
                        "scope": {
                            "type": "string",
                            "enum": ["public", "personal", "private"],
                            "description": (
                                "Sensitivity tier (see system prompt). "
                                "Default to 'personal' if unsure."
                            ),
                        },
```

Append this paragraph to `P2_SYSTEM_PROMPT`, `P3_SYSTEM_PROMPT`, and `P5_SYSTEM_PROMPT` (define it once as a module constant `SCOPE_RUBRIC` and concatenate):

```python
SCOPE_RUBRIC = """
SCOPE — label every question with exactly one sensitivity tier:
- "public": general, shareable facts or shared experiences anyone close to the
  subject could discuss (work, hobbies, public personality, shared events, places).
- "personal": relationship-textured but not sensitive (home life, parenting,
  family rituals, everyday character).
- "private": intimate or sensitive (health, mental health, addiction, conflict,
  money, grief, secrets — anything one would hesitate to share with acquaintances).
When torn between two tiers, choose the more private one.
"""
```

- [ ] **Step 6: Map `scope` into `GeneratedQuestion` in each producer**

`underdeveloped.py` (~line 243):

```python
            q = GeneratedQuestion(
                text=text,
                themes=item["themes"],
                attributes={
                    "subject_centered": True,
                    "supporting_entity": True,
                },
                targets_entity_id=target_id,
                scope=item.get("scope", "personal"),
            )
```

`life_period.py` (~line 107):

```python
            questions.append(
                GeneratedQuestion(
                    text=item["text"],
                    themes=item["themes"],
                    attributes={"life_period": label},
                    scope=item.get("scope", "personal"),
                )
            )
```

`universal.py` (~line 161):

```python
            questions.append(
                GeneratedQuestion(
                    text=item["text"],
                    themes=item["themes"],
                    attributes={"dimension": dimension},
                    scope=item.get("scope", "personal"),
                )
            )
```

- [ ] **Step 7: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/workers/producers/test_scope.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 8: Producer regression**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest tests/workers/producers -q -p no:warnings`
Expected: no new failures vs baseline. (If any test asserts an exact `attributes` dict, update its expectation to include `"scope": "personal"`.)

- [ ] **Step 9: Verify working tree** (`git status --short`; do not commit)

---

## Task 5: Scope + provenance on P4 thread_deepen

**Files:**
- Modify: `src/flashback/workers/thread_detector/schema.py` (`P4Question`)
- Modify: `src/flashback/workers/thread_detector/prompts.py` (`P4_TOOL`, `P4_SYSTEM_PROMPT`)
- Modify: `src/flashback/workers/thread_detector/persistence.py` (`_insert_thread_deepen_question`, its caller, new `_derive_thread_told_by`)
- Test: `tests/workers/thread_detector/test_p4_scope_provenance.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/workers/thread_detector/test_p4_scope_provenance.py`:

```python
from flashback.workers.thread_detector.persistence import _resolve_single_contributor


def test_single_contributor_members_resolve_to_that_user():
    u = "11111111-1111-1111-1111-111111111111"
    # NULL members are unowned; a single distinct collaborator wins.
    assert _resolve_single_contributor([u, None, u]) == u


def test_mixed_contributors_resolve_to_none():
    a = "11111111-1111-1111-1111-111111111111"
    b = "22222222-2222-2222-2222-222222222222"
    assert _resolve_single_contributor([a, b, None]) is None


def test_all_null_members_resolve_to_none():
    assert _resolve_single_contributor([None, None]) is None
    assert _resolve_single_contributor([]) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/workers/thread_detector/test_p4_scope_provenance.py -q -p no:warnings`
Expected: FAIL — `_resolve_single_contributor` does not exist.

- [ ] **Step 3: Add `scope` to `P4Question` + tool + prompt**

`schema.py`:

```python
from typing import Literal
# ...
class P4Question(BaseModel):
    """One ``thread_deepen`` question proposal."""

    model_config = ConfigDict(extra="forbid")

    text: str
    themes: list[str] = Field(min_length=1)
    scope: Literal["public", "personal", "private"] = "personal"
```

`prompts.py` — add to `P4_TOOL` `items.properties` and `required`:

```python
                        "scope": {
                            "type": "string",
                            "enum": ["public", "personal", "private"],
                            "description": (
                                "Sensitivity tier (see system prompt). "
                                "Default to 'personal' if unsure."
                            ),
                        },
```

Append the same `SCOPE_RUBRIC` paragraph (copy the constant from Task 4 into this prompts module, or import it from `flashback.workers.producers.prompts`) to `P4_SYSTEM_PROMPT`.

- [ ] **Step 4: Add the contributor-resolution helper + member-fetch query**

In `src/flashback/workers/thread_detector/persistence.py`:

```python
def _resolve_single_contributor(told_by_values: list[str | None]) -> str | None:
    """Derive a thread_deepen question's told_by from its members.

    NULL members are unowned (creator/shared). If exactly one distinct
    collaborator authored the cluster, stamp that contributor; if two or
    more distinct collaborators contributed, the thread is genuinely
    cross-contributor → NULL (shared). All-NULL → NULL.
    """
    distinct = {v for v in told_by_values if v}
    if len(distinct) == 1:
        return next(iter(distinct))
    return None


def _fetch_member_told_by(cur, moment_ids: list[str]) -> list[str | None]:
    if not moment_ids:
        return []
    cur.execute(
        """
        SELECT told_by_user_id::text
          FROM active_moments
         WHERE id = ANY(%s::uuid[])
        """,
        (moment_ids,),
    )
    return [row[0] for row in cur.fetchall()]
```

- [ ] **Step 5: Stamp `told_by_user_id` + scope in the insert**

Change `_insert_thread_deepen_question` to accept and persist both:

```python
def _insert_thread_deepen_question(
    cur,
    *,
    person_id: str,
    text: str,
    themes: list[str],
    scope: str,
    told_by_user_id: str | None,
    llm_provider: str,
    llm_model: str,
    prompt_version: str,
) -> str:
    cur.execute(
        """
        INSERT INTO questions
              (person_id, text, source, attributes,
               llm_provider, llm_model, prompt_version,
               told_by_user_id)
        VALUES (%s,        %s,   'thread_deepen', %s,
                %s,           %s,        %s,
                %s)
        RETURNING id::text
        """,
        (
            person_id,
            text,
            Json({"themes": themes, "scope": normalize_scope(scope)}),
            llm_provider,
            llm_model,
            prompt_version,
            told_by_user_id,
        ),
    )
    return cur.fetchone()[0]
```

Add `from flashback.questions.scope import normalize_scope` at the top of the module.

- [ ] **Step 6: Wire the caller**

In the P4 question loop (~lines 304–313), derive the contributor once per cluster from the cluster's member moment ids (the same id list passed to `_insert_evidences_edges` in this transaction — read the surrounding function to bind the exact variable; call it `member_moment_ids`):

```python
    told_by = _resolve_single_contributor(
        _fetch_member_told_by(cur, member_moment_ids)
    )
    for q in (p4_result.questions if p4_result is not None else []):
        qid = _insert_thread_deepen_question(
            cur,
            person_id=person_id,
            text=q.text,
            themes=list(q.themes),
            scope=getattr(q, "scope", "personal"),
            told_by_user_id=told_by,
            llm_provider=p4_cfg.provider,
            llm_model=p4_cfg.model,
            prompt_version=P4_PROMPT_VERSION,
        )
```

- [ ] **Step 7: Run the helper test**

Run: `.venv/Scripts/python.exe -m pytest tests/workers/thread_detector/test_p4_scope_provenance.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 8: Thread-detector regression**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest tests/workers/thread_detector -q -p no:warnings`
Expected: no new failures vs baseline. (Update any test asserting the thread_deepen `attributes` dict or insert arity — it now includes `scope` and a `told_by_user_id` column.)

- [ ] **Step 9: Verify working tree** (`git status --short`; do not commit)

---

## Task 6: Scope on P1 dropped_reference (extraction)

**Files:**
- Modify: `src/flashback/workers/extraction/schema.py` (`DroppedReference`)
- Modify: `src/flashback/workers/extraction/prompts.py` (`EXTRACTION_TOOL` dropped_references + system prompt)
- Modify: `src/flashback/workers/extraction/persistence.py` (attrs dict ~line 811)
- Test: `tests/workers/extraction/test_dropped_reference_scope.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/workers/extraction/test_dropped_reference_scope.py`:

```python
from flashback.workers.extraction.schema import DroppedReference


def test_dropped_reference_defaults_scope_personal():
    dr = DroppedReference(dropped_phrase="the cabin", question_text="Tell me about the cabin?", themes=["family"])
    assert dr.scope == "personal"


def test_dropped_reference_accepts_explicit_scope():
    dr = DroppedReference(dropped_phrase="rehab", question_text="What happened then?", themes=["family"], scope="private")
    assert dr.scope == "private"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/workers/extraction/test_dropped_reference_scope.py -q -p no:warnings`
Expected: FAIL — `DroppedReference` has no `scope`.

- [ ] **Step 3: Add `scope` to `DroppedReference`**

`schema.py`:

```python
from typing import Literal
# ...
class DroppedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dropped_phrase: str
    question_text: str
    themes: list[str] = Field(min_length=1)
    scope: Literal["public", "personal", "private"] = "personal"
```

- [ ] **Step 4: Add `scope` to the extraction tool + prompt**

`prompts.py` — in the `dropped_references` `items.properties` add the `scope` enum property and add `"scope"` to its `required` list:

```python
                    "scope": {
                        "type": "string",
                        "enum": ["public", "personal", "private"],
                        "description": (
                            "Sensitivity tier for the resulting question; see "
                            "the scope guidance. Default 'personal' if unsure."
                        ),
                    },
```

Append the `SCOPE_RUBRIC` paragraph (same text as Task 4) to `EXTRACTION_SYSTEM_PROMPT`, framed for dropped references ("Label each dropped_reference's question with its sensitivity tier…").

- [ ] **Step 5: Persist scope into attributes**

`persistence.py` (~line 811), change the attrs dict:

```python
from flashback.questions.scope import normalize_scope
# ...
            attrs = {
                "dropped_phrase": dr.dropped_phrase,
                "themes": list(dr.themes),
                "scope": normalize_scope(getattr(dr, "scope", None)),
            }
```

- [ ] **Step 6: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/workers/extraction/test_dropped_reference_scope.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 7: Extraction regression**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest tests/workers/extraction -q -p no:warnings`
Expected: no new failures vs baseline. (Update any test asserting the dropped_reference `attributes` dict to include `"scope": "personal"`.)

- [ ] **Step 8: Verify working tree** (`git status --short`; do not commit)

---

## Task 7: Collaborator `display_name` (migration 0030 + mirror)

**Files:**
- Create: `migrations/0030_collaborator_onboarding_display_name.up.sql`
- Create: `migrations/0030_collaborator_onboarding_display_name.down.sql`
- Modify: `src/flashback/collaborator_onboarding/queries.py` (`UPSERT_ONBOARDING_SQL`)
- Modify: `src/flashback/collaborator_onboarding/repository.py` (`upsert_onboarding`)
- Modify: `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py`
- Test: `tests/collaborator_onboarding/test_display_name.py` (new, DB-gated)

- [ ] **Step 1: Write the migration files**

`migrations/0030_collaborator_onboarding_display_name.up.sql`:

```sql
ALTER TABLE collaborator_onboarding
    ADD COLUMN IF NOT EXISTS display_name TEXT;
```

`migrations/0030_collaborator_onboarding_display_name.down.sql`:

```sql
ALTER TABLE collaborator_onboarding
    DROP COLUMN IF EXISTS display_name;
```

- [ ] **Step 2: Write the failing DB-gated test**

Create `tests/collaborator_onboarding/test_display_name.py`:

```python
import uuid
import pytest
from flashback.collaborator_onboarding.repository import upsert_onboarding, get_voice_anchor

pytestmark = pytest.mark.asyncio


async def test_display_name_mirrored_and_not_clobbered(db_conn, make_person):
    person_id = await make_person(db_conn)
    user_id = uuid.uuid4()
    await upsert_onboarding(db_conn, person_id=person_id, user_id=user_id,
                            voice_anchor_text="his daughter", display_name="Keerthi")
    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT display_name FROM active_collaborator_onboarding WHERE person_id=%s AND user_id=%s",
            (str(person_id), str(user_id)))
        assert (await cur.fetchone())[0] == "Keerthi"
    # Empty re-mirror must not clobber.
    await upsert_onboarding(db_conn, person_id=person_id, user_id=user_id, display_name=None)
    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT display_name FROM active_collaborator_onboarding WHERE person_id=%s AND user_id=%s",
            (str(person_id), str(user_id)))
        assert (await cur.fetchone())[0] == "Keerthi"
```

- [ ] **Step 3: Run to verify it fails** (apply migration 0030 first against the test DB)

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest tests/collaborator_onboarding/test_display_name.py -q -p no:warnings`
Expected: FAIL — `upsert_onboarding` has no `display_name` kwarg / column.

- [ ] **Step 4: Add `display_name` to the upsert SQL**

In `src/flashback/collaborator_onboarding/queries.py`, add `display_name` to the INSERT column list + `VALUES` placeholder, and to the `ON CONFLICT ... DO UPDATE SET` with the non-clobber COALESCE (mirror the existing `voice_anchor_text` pattern):

```sql
        display_name = COALESCE(EXCLUDED.display_name, collaborator_onboarding.display_name),
```

- [ ] **Step 5: Add the kwarg to the repository helper**

In `src/flashback/collaborator_onboarding/repository.py`, add `display_name: str | None = None` to `upsert_onboarding`'s keyword args and bind it into the params passed to `UPSERT_ONBOARDING_SQL` (matching the new placeholder).

- [ ] **Step 6: Pass it from the apply step**

In `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py`, read `display_name` from session metadata and pass to the upsert:

```python
        display_name = state.session_metadata.get("contributor_display_name")
        # ... inside the existing upsert_onboarding(...) call:
            display_name=str(display_name).strip() or None if display_name else None,
```

- [ ] **Step 7: Run the test**

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest tests/collaborator_onboarding/test_display_name.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 8: Onboarding + apply-step regression (no-DB)**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest tests/orchestrator/test_apply_collaborator_onboarding.py -q -p no:warnings`
Expected: no new failures.

- [ ] **Step 9: Verify working tree** (`git status --short`; do not commit)

---

## Task 8: Seed `scope='public'` on coverage taps (migration 0031)

**Files:**
- Create: `migrations/0031_coverage_tap_scope_public.up.sql`
- Create: `migrations/0031_coverage_tap_scope_public.down.sql`

- [ ] **Step 1: Write the migration files**

`migrations/0031_coverage_tap_scope_public.up.sql`:

```sql
UPDATE questions
   SET attributes = jsonb_set(COALESCE(attributes, '{}'::jsonb), '{scope}', '"public"', true)
 WHERE source = 'coverage_tap'
   AND person_id IS NULL
   AND COALESCE(attributes->>'scope', '') <> 'public';
```

`migrations/0031_coverage_tap_scope_public.down.sql`:

```sql
UPDATE questions
   SET attributes = attributes - 'scope'
 WHERE source = 'coverage_tap'
   AND person_id IS NULL;
```

- [ ] **Step 2: Apply + verify (DB-gated, manual check)**

Run against the test DB, then:

```sql
SELECT count(*) FROM questions
 WHERE source='coverage_tap' AND person_id IS NULL
   AND attributes->>'scope' <> 'public';
```

Expected: `0`. Re-running the up migration is idempotent (the `<>` guard).

- [ ] **Step 3: Verify working tree** (`git status --short`; do not commit)

---

## Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update invariant #26**

In the §4 invariant #26 paragraph, change the clause stating cadence/thread_deepen producer runs are always NULL to reflect derivation: "`thread_deepen` questions derive `told_by_user_id` from their cluster's member moments — a single contributing collaborator is stamped; a genuinely cross-contributor (multi-collaborator) cluster stays NULL."

- [ ] **Step 2: Add a new invariant for scope tiers**

Append invariant #27 to §4:

```
27. **Question eligibility is provenance + scope gated.** Every produced
    question carries `attributes.scope ∈ {public, personal, private}`
    (LLM-labelled, code-normalized; missing/unknown → `personal`). The
    selector admits a question for contributor `Y` (NULL = creator era)
    iff: `public` (all); `personal` AND (`told_by_user_id` IS NULL OR =
    Y); or `private` AND `told_by_user_id` IS NOT DISTINCT FROM Y. The
    LLM picks the label; SQL enforces the gate (code over LLM, §10).
    Coverage taps are seeded `public`. Relationship-based `personal`
    (close family of the subject) is deferred.
```

- [ ] **Step 3: Verify working tree** (`git status --short`; do not commit)

---

## Final verification

- [ ] **Full no-DB suite at baseline**

Run: `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
Expected: 14 failures, none new (diff the FAILED list against the recorded baseline).

- [ ] **Full DB-gated suite at baseline** (when Postgres is up + migrations 0030/0031 applied)

Run: `TEST_DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
Expected: 28 failures, none new, plus the new SP4 tests green.

- [ ] **Manual leak reproduction (optional, via local dev UI):** creator session asks no collaborator-stamped question; a fresh collaborator's session asks no other collaborator's `personal`/`private` question; `public` family lore still flows.
