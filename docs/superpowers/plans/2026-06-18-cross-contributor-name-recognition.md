# Cross-contributor Name Recognition (lite) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a contributor mentions an entity another contributor introduced, the agent recognizes it AND credits the source ("Priya — Ravi's the one who told us about her"), by surfacing the entity's contributor provenance in `<mentioned_entities>`.

**Architecture:** Pure pattern-reuse of the SP2/SP3 moment-attribution mechanism, applied to entities. `entities.told_by_user_id` (already stamped, never restamped on reuse) → `LEFT JOIN collaborator_onboarding` resolves the contributor's display name + relationship → `EntityResult` carries the same `told_by_*` fields as `MomentResult` → the `<mentioned_entities>` renderer adds `told_by`/`relationship` attrs on cross-contributor entities → a base-prompt instruction lets the agent acknowledge it. No migration, no merge, no retrieval/selection change.

**Tech Stack:** Python, psycopg (async pool, `dict_row`), Pydantic, Postgres, pytest (`asyncio_mode=auto`).

## Global Constraints

- **NO COMMITS THIS CYCLE.** All work lands in the working tree on `feature/collaborator-provenance`; the user commits. **Skip every `git add`/`git commit` step** — each task ends with `git status --short`.
- **Test command (no-DB):** `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
- **Test command (DB-gated):** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings` (Postgres up: docker `flashback-postgres`, db `flashback_test`, role `flashback`). DB-gated tests skip when `TEST_DATABASE_URL` unset.
- **Judge regressions by diffing the FAILED list against baseline**, not absolute counts (the branch carries pre-existing unrelated failures).
- Scope: the **`get_entities_by_ids` (entity-mention scanner)** surface only. `search_entities` is out of scope. No identity merging. Entities are NOT scope-gated.
- Field names on `EntityResult` mirror `MomentResult` exactly: `told_by_user_id`, `told_by_display_name`, `told_by_relationship`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/flashback/retrieval/schema.py` | `EntityResult` gains `told_by_*` fields | 1 |
| `src/flashback/retrieval/queries.py` | `GET_ENTITIES_BY_IDS_SQL` join for name+relationship | 1 |
| `src/flashback/response_generator/context.py` | `<mentioned_entities>` cross-contributor attribution | 2 |
| `src/flashback/response_generator/prompts.py` | base-prompt acknowledgment instruction | 3 |
| `CLAUDE.md` | note under invariant #20 | 4 |

`src/flashback/retrieval/service.py` needs **no change**: `_fetch_all` returns `dict` rows and `get_entities_by_ids` does `EntityResult.model_validate(row)`, so the new SQL columns auto-map to the new fields.

---

## Task 1: Entity provenance — schema field + query join

**Files:**
- Modify: `src/flashback/retrieval/schema.py` (`EntityResult`)
- Modify: `src/flashback/retrieval/queries.py` (`GET_ENTITIES_BY_IDS_SQL`)
- Test: `tests/retrieval/test_entity_provenance.py` (new, DB-gated) + a schema unit test

**Interfaces:**
- Produces (used by Task 2): `EntityResult.told_by_user_id: UUID | None`, `EntityResult.told_by_display_name: str | None`, `EntityResult.told_by_relationship: str | None` (all default `None`), populated by `RetrievalService.get_entities_by_ids`.

- [ ] **Step 1: Write the failing tests**

First read `tests/retrieval/conftest.py` and `tests/retrieval/test_relationship_attribution.py` to reuse the exact DB fixtures (pool/connection fixture, person-insert helper, how entities + `collaborator_onboarding` rows are inserted, and how `RetrievalService` is constructed). Create `tests/retrieval/test_entity_provenance.py` (adapt fixture names to what those files use):

```python
import uuid
from datetime import datetime, timezone
import pytest

pytestmark = pytest.mark.asyncio


async def _insert_entity(conn, person_id, *, name, told_by):
    eid = uuid.uuid4()
    await conn.execute(
        """INSERT INTO entities (id, person_id, kind, name, aliases, attributes, told_by_user_id)
           VALUES (%s, %s, 'person', %s, '{}', '{}'::jsonb, %s)""",
        (str(eid), str(person_id), name, str(told_by) if told_by else None),
    )
    return eid


async def test_collaborator_entity_resolves_name_and_relationship(retrieval_service, db_conn, make_person):
    person_id = await make_person(db_conn)
    ravi = uuid.uuid4()
    # collaborator onboarding row gives the name + relationship
    await db_conn.execute(
        """INSERT INTO collaborator_onboarding (person_id, user_id, voice_anchor_text, voice_anchored_at, display_name)
           VALUES (%s, %s, 'his son', now(), 'Ravi')""",
        (str(person_id), str(ravi)),
    )
    eid = await _insert_entity(db_conn, person_id, name="Priya", told_by=ravi)
    await db_conn.commit()
    [ent] = await retrieval_service.get_entities_by_ids(person_id, [eid])
    assert ent.told_by_user_id == ravi
    assert ent.told_by_display_name == "Ravi"
    assert ent.told_by_relationship == "his son"


async def test_null_told_by_entity_has_no_provenance(retrieval_service, db_conn, make_person):
    person_id = await make_person(db_conn)
    eid = await _insert_entity(db_conn, person_id, name="Comet", told_by=None)
    await db_conn.commit()
    [ent] = await retrieval_service.get_entities_by_ids(person_id, [eid])
    assert ent.told_by_user_id is None
    assert ent.told_by_display_name is None
    assert ent.told_by_relationship is None


async def test_told_by_without_onboarding_row_has_no_name(retrieval_service, db_conn, make_person):
    person_id = await make_person(db_conn)
    orphan = uuid.uuid4()  # a user_id with no collaborator_onboarding row
    eid = await _insert_entity(db_conn, person_id, name="Anuj", told_by=orphan)
    await db_conn.commit()
    [ent] = await retrieval_service.get_entities_by_ids(person_id, [eid])
    assert ent.told_by_user_id == orphan
    assert ent.told_by_display_name is None
    assert ent.told_by_relationship is None
```

Also add a no-DB schema test in the same file (or `tests/retrieval/test_schema.py` if it exists):

```python
def test_entity_result_provenance_defaults_none():
    from flashback.retrieval.schema import EntityResult
    from datetime import datetime, timezone
    import uuid
    e = EntityResult(id=uuid.uuid4(), person_id=uuid.uuid4(), kind="person",
                     name="X", description=None, aliases=[], attributes={},
                     created_at=datetime.now(timezone.utc))
    assert e.told_by_user_id is None
    assert e.told_by_display_name is None
    assert e.told_by_relationship is None
```

> Match the real fixtures: `retrieval_service`/`db_conn`/`make_person` are placeholders — use whatever `tests/retrieval/conftest.py` actually provides (the moment provenance tests in `test_relationship_attribution.py` already do exactly this kind of setup; mirror them).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/retrieval/test_entity_provenance.py -q -p no:warnings`
Expected: FAIL (no `told_by_*` on `EntityResult`; query doesn't return those columns).

- [ ] **Step 3: Add the fields to `EntityResult`**

In `src/flashback/retrieval/schema.py`, add to `EntityResult` (after `created_at`):

```python
    told_by_user_id: UUID | None = None
    told_by_display_name: str | None = None
    told_by_relationship: str | None = None
```

(`UUID` is already imported in this module — confirm.)

- [ ] **Step 4: Add the join to `GET_ENTITIES_BY_IDS_SQL`**

In `src/flashback/retrieval/queries.py`, replace `GET_ENTITIES_BY_IDS_SQL` with (mirrors the `SEARCH_MOMENTS_SQL` join — `collaborator_onboarding` directly, `status='active'`):

```python
GET_ENTITIES_BY_IDS_SQL = """
SELECT e.id, e.person_id, e.kind, e.name, e.description, e.aliases,
       e.attributes, e.created_at,
       e.told_by_user_id,
       co.display_name      AS told_by_display_name,
       co.voice_anchor_text AS told_by_relationship
FROM   active_entities e
LEFT JOIN collaborator_onboarding co
       ON co.person_id = e.person_id
      AND co.user_id   = e.told_by_user_id
      AND co.status    = 'active'
WHERE  e.person_id = %(person_id)s
  AND  e.id        = ANY(%(entity_ids)s)
ORDER  BY e.created_at DESC
"""
```

(`active_entities` exposes `told_by_user_id` since migration 0029. `collaborator_onboarding.display_name` exists since migration 0030.)

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/retrieval/test_entity_provenance.py -q -p no:warnings`
Expected: PASS (4 — three DB-gated + the schema default).

- [ ] **Step 6: Retrieval regression**

Run: `.venv/Scripts/python.exe -m pytest tests/retrieval -q -p no:warnings`
Expected: no new failures vs baseline.

- [ ] **Step 7: Verify working tree** — `git status --short` (do not commit).

---

## Task 2: Render cross-contributor attribution in `<mentioned_entities>`

**Files:**
- Modify: `src/flashback/response_generator/context.py` (the `<mentioned_entities>` block in `render_turn_context`)
- Test: `tests/response_generator/test_attribution_render.py` (extend)

**Interfaces:**
- Consumes: `EntityResult.told_by_user_id`/`told_by_display_name`/`told_by_relationship` (Task 1); `TurnContext.current_user_id`, `TurnContext.mentioned_entities`, `TurnContext.ambiguous_mention` (existing).

- [ ] **Step 1: Write the failing test**

Read `tests/response_generator/test_attribution_render.py` to reuse its `TurnContext`-building helper (it already tests moment `told_by` rendering — mirror that for entities). Add tests asserting the `<mentioned_entities>` block carries `told_by`/`relationship` for a cross-contributor entity and not otherwise. Build `EntityResult`s with the new fields and a `TurnContext` with `current_user_id` set. Target assertions:

```python
# cross-contributor entity (told_by != current, name present) -> attributed
assert 'told_by="Ravi"' in rendered
assert 'relationship="his son"' in rendered
# own-contributor entity (told_by == current) -> no told_by
# null-provenance entity -> no told_by
# entity with told_by name but no relationship -> told_by only, no relationship=
```

Use the same render entry point the moment-attribution test uses (e.g. `render_turn_context(ctx)`), and construct `EntityResult` with the Task 1 fields. Match the existing test's exact `TurnContext` construction (current_user_id, mentioned_entities, ambiguous_mention).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q -p no:warnings`
Expected: FAIL (no `told_by` rendered for entities yet).

- [ ] **Step 3: Implement the render**

In `src/flashback/response_generator/context.py`, the current `<mentioned_entities>` block is:

```python
    if ctx.mentioned_entities:
        lines = []
        for entity in ctx.mentioned_entities:
            description = entity.description or ""
            lines.append(
                f"- {entity.kind} {xml_text(entity.name)}: {xml_text(description)}".rstrip()
            )
        attrs = ' ambiguous="true"' if ctx.ambiguous_mention else ""
        sections.append(
            "\n".join(
                [
                    f"<mentioned_entities{attrs}>",
                    "\n".join(lines),
                    "</mentioned_entities>",
                ]
            )
        )
```

Replace the `for` loop body so each line gets cross-contributor attribution (identical guard to the moment block above it in the same function):

```python
        lines = []
        for entity in ctx.mentioned_entities:
            description = entity.description or ""
            attribution = ""
            if (
                ctx.current_user_id is not None
                and entity.told_by_user_id is not None
                and entity.told_by_user_id != ctx.current_user_id
                and entity.told_by_display_name
            ):
                attribution = f' told_by="{xml_text(entity.told_by_display_name)}"'
                if entity.told_by_relationship:
                    attribution += f' relationship="{xml_text(entity.told_by_relationship)}"'
            lines.append(
                f"- {entity.kind} {xml_text(entity.name)}: {xml_text(description)}".rstrip()
                + attribution
            )
```

(Leave the `attrs`/`sections.append` part unchanged.)

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_attribution_render.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Response-generator regression**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator -q -p no:warnings`
Expected: no new failures.

- [ ] **Step 6: Verify working tree** — `git status --short`.

---

## Task 3: Base-prompt acknowledgment instruction

**Files:**
- Modify: `src/flashback/response_generator/prompts.py` (`BASE_SYSTEM_PROMPT`)
- Test: `tests/response_generator/test_prompts.py` (extend)

**Interfaces:**
- Consumes: nothing new. Mirrors the existing `_TAP_PENDING_NOTE`-in-base pattern and the `RECALL_PROMPT` ATTRIBUTION wording.

- [ ] **Step 1: Write the failing test**

Read `tests/response_generator/test_prompts.py` — find the test that asserts `BASE_SYSTEM_PROMPT` is in every intent prompt (e.g. `test_base_system_prompt_is_in_every_intent_prompt`). Add a test asserting the new instruction is present in the base prompt (and therefore every intent family):

```python
def test_base_prompt_has_mentioned_entity_attribution():
    from flashback.response_generator.prompts import (
        BASE_SYSTEM_PROMPT, RECALL_PROMPT, STORY_PROMPT, SWITCH_PROMPT,
    )
    assert "mentioned_entities" in BASE_SYSTEM_PROMPT
    # carried into every family via BASE_SYSTEM_PROMPT
    for p in (RECALL_PROMPT, STORY_PROMPT, SWITCH_PROMPT):
        assert "mentioned_entities" in p
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_prompts.py -q -p no:warnings`
Expected: FAIL (instruction not present).

- [ ] **Step 3: Add the instruction to `BASE_SYSTEM_PROMPT`**

In `src/flashback/response_generator/prompts.py`, define a conditional note constant near the existing `_TAP_PENDING_NOTE` (above `BASE_SYSTEM_PROMPT`):

```python
# Conditional note — placed in BASE_SYSTEM_PROMPT so every intent family
# inherits it once. Conditional phrasing ("If a <mentioned_entities> line
# carries told_by") makes it a no-op when no such attribution is present.
_MENTIONED_ENTITY_ATTRIBUTION_NOTE = """

CROSS-CONTRIBUTOR RECOGNITION: If a line inside <mentioned_entities> carries a
told_by="Name" attribute, a DIFFERENT contributor (not the person you are
speaking with now) is the one who first told us about that person or place. You
MAY naturally acknowledge the connection ("Priya - Ravi's the one who first told
us about her"), crediting the name, and the relationship="..." too when present.
Keep it light and natural - never force it, never restate it mechanically, and
never claim the current contributor introduced them. Lines with no told_by are
this contributor's own or shared knowledge; reference them without crediting
anyone."""
```

Then append it to `BASE_SYSTEM_PROMPT` (the same way `_TAP_PENDING_NOTE` is appended — find how `BASE_SYSTEM_PROMPT` currently incorporates `_TAP_PENDING_NOTE` and add `+ _MENTIONED_ENTITY_ATTRIBUTION_NOTE` consistently, so the base prompt ends with both notes).

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator/test_prompts.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Regression**

Run: `.venv/Scripts/python.exe -m pytest tests/response_generator -q -p no:warnings`
Expected: no new failures.

- [ ] **Step 6: Verify working tree** — `git status --short`.

---

## Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update invariant #20**

In `CLAUDE.md`, find invariant #20 (the deterministic entity-mention scanner). Add a brief sentence: the `<mentioned_entities>` block now also surfaces cross-contributor provenance — an entity's `told_by_user_id` (first introducer, never restamped on reuse) is resolved to a `told_by`/`relationship` attribution via `collaborator_onboarding` when a *different* contributor mentions it, letting the agent credit the source ("the one Ravi mentioned"). Note it's recognition-only (no merge) and entities remain un-scope-gated.

- [ ] **Step 2: Verify working tree** — `git status --short`.

---

## Final verification

- [ ] **Full no-DB suite at baseline** — `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings` — diff FAILED list vs baseline; zero new.
- [ ] **Full DB-gated suite** — `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings` — new tests green; zero new failures.
- [ ] **Manual (optional, dev UI):** as a collaborator, mention a person another collaborator introduced; the agent acknowledges "the one [name] mentioned." Mention your own / a creator-introduced entity → recognized but not name-attributed.
