# Collaborator Provenance Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every contributor-authored row in the canonical graph records which Node `user_id` authored it; the legacy `role_id` contract field is retired.

**Architecture:** Thread-through. `user_id` enters on the HTTP request, replaces `role_id` in Working Memory and orchestrator state, rides the extraction/producer/profile-summary queue payloads, and persistence stamps it at insert. Write-path only — no read path consumes the new columns yet (sub-projects 2–6 do).

**Tech Stack:** Python, FastAPI + pydantic v2, psycopg (raw SQL), Valkey, SQS. Tests: pytest (`python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-06-12-collaborator-provenance-foundation-design.md`

**Key spec decisions this plan implements:**
- D1: provenance = Node `user_id`; `role_id` retired (tolerated-and-ignored on requests during transition).
- D2: `user_id` optional in the contract; NULL = creator era; no backfill.
- D3: columns on `moments` (+display name), `entities`, `traits`, `questions`, `profile_facts`, `processed_extractions`. NOT on threads/themes.
- D4: stamping is code-side only (LLM never sees it); entity reuse-folds and trait merge-updates do NOT restamp; supersession carries the refining segment's authorship (automatic — refinements insert new moment rows).

---

### Task 1: Migration 0026 — provenance columns

**Files:**
- Create: `migrations/0026_contributor_provenance.up.sql`
- Create: `migrations/0026_contributor_provenance.down.sql`

There is no migration-runner test harness in this repo; migrations are verified by applying them to the local dev database. Follow the style of `migrations/0025_extraction_completion_signal.up.sql` (header comment block, `BEGIN;`/`COMMIT;`).

- [ ] **Step 1: Write the up migration**

```sql
-- ============================================================================
-- 0026_contributor_provenance.up.sql
-- Collaborator Phase 1, sub-project 1: provenance foundation.
-- ----------------------------------------------------------------------------
-- Every contributor-authored row records the Node user who authored it.
-- NULL means "creator era" (rows written before multi-contributor existed,
-- or rows produced without a session user — seeded questions, cadence
-- producer runs). No backfill, by design.
--
-- Semantics per table (spec D3):
--   moments.told_by_user_id        — told by (load-bearing: attribution,
--                                    retrieval bias, removal, conflicts)
--   moments.told_by_display_name   — denormalized for attribution rendering
--   entities.told_by_user_id       — first introduced by (informational)
--   traits.told_by_user_id         — first asserted by (informational)
--   questions.told_by_user_id      — whose session motivated it
--   profile_facts.told_by_user_id  — whose session produced the answer
--   processed_extractions.told_by_user_id — segment bookkeeping
--
-- Only moments.told_by_user_id ever drives hiding/removal (spec D4).
-- ============================================================================

BEGIN;

ALTER TABLE moments
    ADD COLUMN told_by_user_id      UUID NULL,
    ADD COLUMN told_by_display_name TEXT NULL;

ALTER TABLE entities
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE traits
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE questions
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE profile_facts
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE processed_extractions
    ADD COLUMN told_by_user_id UUID NULL;

-- Speaker-first retrieval (sub-project 2) and removal (sub-project 6)
-- both filter on exactly (person_id, told_by_user_id) over active rows.
CREATE INDEX moments_person_told_by_active_idx
    ON moments (person_id, told_by_user_id)
    WHERE status = 'active';

COMMIT;
```

- [ ] **Step 2: Write the down migration**

```sql
-- ============================================================================
-- 0026_contributor_provenance.down.sql
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS moments_person_told_by_active_idx;

ALTER TABLE processed_extractions DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE profile_facts         DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE questions             DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE traits                DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE entities              DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE moments               DROP COLUMN IF EXISTS told_by_display_name;
ALTER TABLE moments               DROP COLUMN IF EXISTS told_by_user_id;

COMMIT;
```

- [ ] **Step 3: Verify the SQL round-trips on the local dev database**

If a local Postgres with the schema is available, apply up then down then up
(`psql "$DATABASE_URL" -f migrations/0026_contributor_provenance.up.sql` etc.).
If no local database is configured in this environment, skip execution — the
file review in Step 4 is the gate.

- [ ] **Step 4: Self-check the SQL**

Confirm: every `ADD COLUMN` is `NULL`-able with no default; the index is partial on `status = 'active'`; the down file drops in reverse order. Check `migrations/0001_initial_schema.up.sql` to confirm table names `moments`, `entities`, `traits`, `questions` and `migrations/0010_profile_facts.up.sql` / `0003_extraction_worker_support.up.sql` for `profile_facts` / `processed_extractions` exist as named.

- [ ] **Step 5: Commit**

```bash
git add migrations/0026_contributor_provenance.up.sql migrations/0026_contributor_provenance.down.sql
git commit -m "feat(collaborator): migration 0026 — told_by provenance columns"
```

---

### Task 2: HTTP request models — add `user_id`, retire `role_id`

**Files:**
- Modify: `src/flashback/http/models.py:52-94` (`SessionStartRequest`, `TurnRequest`)
- Test: `tests/http/test_models_provenance.py` (create)

The models use `model_config = ConfigDict(extra="forbid")`, so an un-updated Node sending `role_id` would 422 if we simply deleted the field. Tolerance therefore means: keep a declared `role_id` field, optional, documented as ignored.

- [ ] **Step 1: Write the failing tests**

Create `tests/http/test_models_provenance.py`:

```python
"""Contract tests for the user_id provenance field (spec D1/D2)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from flashback.http.models import SessionStartRequest, TurnRequest


def _session_body(**overrides):
    body = {"session_id": str(uuid4()), "person_id": str(uuid4())}
    body.update(overrides)
    return body


def _turn_body(**overrides):
    body = {
        "session_id": str(uuid4()),
        "person_id": str(uuid4()),
        "message": "hello",
    }
    body.update(overrides)
    return body


class TestSessionStartRequest:
    def test_accepts_user_id(self):
        uid = uuid4()
        req = SessionStartRequest(**_session_body(user_id=str(uid)))
        assert req.user_id == uid

    def test_user_id_defaults_to_none(self):
        req = SessionStartRequest(**_session_body())
        assert req.user_id is None

    def test_legacy_role_id_tolerated_and_ignored(self):
        # An un-updated Node still sends role_id; it must not 422 and
        # must not become provenance.
        req = SessionStartRequest(**_session_body(role_id=str(uuid4())))
        assert req.user_id is None

    def test_rejects_malformed_user_id(self):
        with pytest.raises(ValidationError):
            SessionStartRequest(**_session_body(user_id="not-a-uuid"))


class TestTurnRequest:
    def test_accepts_user_id(self):
        uid = uuid4()
        req = TurnRequest(**_turn_body(user_id=str(uid)))
        assert req.user_id == uid

    def test_user_id_defaults_to_none(self):
        req = TurnRequest(**_turn_body())
        assert req.user_id is None

    def test_legacy_role_id_tolerated_and_ignored(self):
        req = TurnRequest(**_turn_body(role_id=str(uuid4())))
        assert req.user_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/http/test_models_provenance.py -v`
Expected: FAIL — `user_id` not a field; bodies without `role_id` raise ValidationError (it is currently required).

- [ ] **Step 3: Update the models**

In `src/flashback/http/models.py`, change `SessionStartRequest`:

```python
class SessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    # The authoring Node user. Optional during the contract transition;
    # NULL provenance = "creator era" (spec D2). This is the ONLY
    # identity field — see role_id below.
    user_id: UUID | None = None
    # DEPRECATED (spec D1): retired v1 field with no Node-side concept
    # behind it. Declared only so extra="forbid" doesn't 422 an
    # un-updated Node. Never read; never provenance. Remove once Node
    # ships user_id.
    role_id: UUID | None = None
    contributor_display_name: str | None = None
    session_metadata: dict = Field(default_factory=dict)
    mode: Mode = "text"
```

and `TurnRequest`:

```python
class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    user_id: UUID | None = None
    # DEPRECATED: tolerated and ignored — see SessionStartRequest.role_id.
    role_id: UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    question_decision: QuestionDecisionInput | None = None
    mode: Mode = "text"
```

(The stream endpoints reuse these same models — confirm with `grep -n "SessionStartRequest\|TurnRequest" src/flashback/http/routes/stream.py` — so no separate stream models exist to change.)

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest tests/http/test_models_provenance.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Run the existing http-model/route tests to see what the rename breaks**

Run: `python -m pytest tests/http -x -q`
Expected: failures in route tests that construct requests with `role_id` and in route code that reads `body.role_id`. Do NOT fix the routes yet (Task 4 does); if failures are only in files Task 4 touches, note them and move on. If a test asserts `role_id` is required, update that test to assert the new optional behavior.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/http/models.py tests/http/test_models_provenance.py
git commit -m "feat(collaborator): user_id on session/turn requests; role_id deprecated"
```

---

### Task 3: Working Memory — rename `role_id` → `user_id`

**Files:**
- Modify: `src/flashback/working_memory/schema.py:68,168` (state field + serializer)
- Modify: `src/flashback/working_memory/client.py:87-149` (`initialize`)
- Test: `tests/working_memory/` (existing tests reference `role_id`; update) + new round-trip test

- [ ] **Step 1: Write the failing test**

Add to the existing working-memory test module (find it: `ls tests/working_memory/`), or create `tests/working_memory/test_user_id_roundtrip.py`:

```python
"""user_id replaces role_id in WM state (spec D1)."""

from datetime import datetime, timezone

from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def test_user_id_round_trips_through_serialise_and_parse():
    state = WorkingMemoryState(
        person_id="p1",
        user_id="11111111-1111-1111-1111-111111111111",
        started_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )
    mapping = serialise_state_for_init(state)
    assert mapping["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert "role_id" not in mapping

    parsed = parse_state_hash(mapping)
    assert parsed.user_id == "11111111-1111-1111-1111-111111111111"


def test_user_id_defaults_to_empty_string():
    # Sessions started before Node sends user_id hydrate with "".
    state = WorkingMemoryState(
        person_id="p1",
        started_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )
    assert state.user_id == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/working_memory/test_user_id_roundtrip.py -v`
Expected: FAIL — `user_id` is not a field on `WorkingMemoryState`.

- [ ] **Step 3: Implement**

In `src/flashback/working_memory/schema.py`:

1. Line 68: replace `role_id: str` with `user_id: str = ""` (the default makes hydration of pre-existing live sessions, whose HASH has a `role_id` key but no `user_id` key, fall back cleanly).
2. In `parse_state_hash`, drop unknown keys instead of passing them through — a live session's HASH still contains `role_id`, and `extra="forbid"` on the model would reject it. Replace the final `else` branch:

```python
        else:
            parsed[key] = value
    return WorkingMemoryState.model_validate(parsed)
```

with:

```python
        else:
            parsed[key] = value
    # Live sessions started before the rename still carry a role_id key
    # in the HASH; the model no longer has that field (spec D1).
    parsed.pop("role_id", None)
    return WorkingMemoryState.model_validate(parsed)
```

3. In `serialise_state_for_init` (line 168): replace `"role_id": state.role_id,` with `"user_id": state.user_id,`.

In `src/flashback/working_memory/client.py` `initialize` (line 87): rename the parameter `role_id: str` to `user_id: str = ""` and the constructor kwarg `role_id=role_id` (line 136) to `user_id=user_id`.

- [ ] **Step 4: Run the new test, then the whole WM suite**

Run: `python -m pytest tests/working_memory -q`
Expected: the new tests PASS; existing tests that pass `role_id=` to `initialize` or assert on `state.role_id` FAIL. Update those call sites/assertions to `user_id` (mechanical; find them with `grep -rn "role_id" tests/working_memory`). Re-run until green.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/working_memory tests/working_memory
git commit -m "feat(collaborator): rename WM role_id to user_id"
```

---

### Task 4: Orchestrator + routes — rename `role_id` → `user_id` end-to-end

**Files (every remaining `role_id` site in src — verify list with `grep -rn "role_id" src/`):**
- Modify: `src/flashback/orchestrator/state.py:32,69` (`TurnState.role_id`, `SessionStartState.role_id`)
- Modify: `src/flashback/orchestrator/protocol.py:114-165` (6 signature sites)
- Modify: `src/flashback/orchestrator/orchestrator.py` (~20 sites: handler params, state construction, `wm.initialize(...)` calls)
- Modify: `src/flashback/orchestrator/steps/starter_opener.py:246`
- Modify: `src/flashback/http/routes/session.py:54,63`
- Modify: `src/flashback/http/routes/turn.py:121`
- Modify: `src/flashback/http/routes/stream.py:129,187`
- Modify: `src/flashback/http/routes/onboarding.py:139`
- Test: existing `tests/orchestrator/`, `tests/http/` suites

This is a mechanical rename with two judgment points called out below.

- [ ] **Step 1: Rename in orchestrator state**

In `src/flashback/orchestrator/state.py`, change both dataclasses:

```python
    # TurnState (line 32) and SessionStartState (line 69):
    user_id: UUID | None = None   # was: role_id: UUID
```

Because dataclass fields with defaults must follow fields without, move the field below the last required field in each dataclass (after `started_at` in `TurnState` — keep `user_message`/`session_metadata` ordering intact; after `started_at` in `SessionStartState`). Check each dataclass's existing default boundary before placing it.

- [ ] **Step 2: Rename in protocol, orchestrator, starter_opener**

Mechanical: in `protocol.py`, `orchestrator.py`, `starter_opener.py` replace every `role_id` token with `user_id` and every `role_id: UUID` annotation with `user_id: UUID | None`. Where `orchestrator.py` stringifies for WM (`role_id=str(state.role_id)` at lines 107, 160, 216, 252, 302, 385, 430, 558, 601, 649, 682, 714), the None case must serialize as `""` not `"None"`:

```python
            user_id=str(state.user_id) if state.user_id else "",
```

- [ ] **Step 3: Rename in routes**

- `session.py:54` (orchestrator call) and `:63` (`wm.initialize`): `user_id=body.user_id` / `user_id=str(body.user_id) if body.user_id else ""`.
- `turn.py:121`: `user_id=body.user_id`.
- `stream.py:129,187`: `user_id=body.user_id`.
- `onboarding.py:139`: this line currently fakes identity with `role_id=person.person_id`. Do NOT carry the fake over — the onboarding-started session has no Node user in scope yet, so pass `user_id=None`:

```python
        user_id=None,  # onboarding has no Node user in scope; creator-era NULL (spec D2)
```

- [ ] **Step 4: Verify no `role_id` remains outside the deprecated model fields**

Run: `grep -rn "role_id" src/`
Expected: matches ONLY in `src/flashback/http/models.py` (the two deprecated fields + comments).

- [ ] **Step 5: Run the orchestrator + http suites, fix mechanical fallout**

Run: `python -m pytest tests/orchestrator tests/http -q`
Expected: failures where tests construct `TurnState(role_id=...)` / call handlers with `role_id=`. Update them to `user_id` (find with `grep -rln "role_id" tests/`). A test constructing `SessionStartRequest(role_id=...)` and asserting it flows to WM should now assert WM receives `user_id=""` (legacy field ignored). Re-run until green.

- [ ] **Step 6: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS. The rename is complete and nothing else has changed behavior.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "feat(collaborator): thread user_id through orchestrator and routes; retire role_id"
```

---

### Task 5: Extraction queue payload carries `told_by_user_id`

**Files:**
- Modify: `src/flashback/queues/extraction.py:18-50` (`push`)
- Modify: `src/flashback/workers/extraction/schema.py:246-266` (`ExtractionMessage`)
- Modify: `src/flashback/orchestrator/steps/detect_segment.py:104-114` (push site 1)
- Modify: `src/flashback/orchestrator/steps/wrap_session.py:128-137` (push site 2)
- Test: `tests/queues/` (producer payload), `tests/orchestrator/` (push sites), `tests/workers/extraction/` (message parse)

- [ ] **Step 1: Write the failing tests**

In the existing extraction-queue producer test module (find with `grep -rln "ExtractionQueueProducer" tests/`), add:

```python
async def test_push_includes_told_by_user_id(...):
    # using the module's existing fake SQS client fixture pattern
    await producer.push(
        session_id=session_id,
        person_id=person_id,
        segment_turns=[],
        rolling_summary="",
        prior_rolling_summary="",
        seeded_question_id=None,
        told_by_user_id="11111111-1111-1111-1111-111111111111",
    )
    payload = fake_sqs.last_payload
    assert payload["told_by_user_id"] == "11111111-1111-1111-1111-111111111111"


async def test_push_omitted_user_id_serialises_null(...):
    await producer.push(
        session_id=session_id,
        person_id=person_id,
        segment_turns=[],
        rolling_summary="",
        prior_rolling_summary="",
        seeded_question_id=None,
    )
    assert fake_sqs.last_payload["told_by_user_id"] is None
```

(Adapt fixture names to the module's existing pattern — read the file first.)

In `tests/workers/extraction/` message-schema tests, add:

```python
def test_extraction_message_parses_told_by_user_id():
    msg = ExtractionMessage.model_validate(
        {
            "session_id": str(uuid4()),
            "person_id": str(uuid4()),
            "segment_turns": [],
            "told_by_user_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    assert str(msg.told_by_user_id) == "11111111-1111-1111-1111-111111111111"


def test_extraction_message_told_by_defaults_none():
    msg = ExtractionMessage.model_validate(
        {
            "session_id": str(uuid4()),
            "person_id": str(uuid4()),
            "segment_turns": [],
        }
    )
    assert msg.told_by_user_id is None
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/queues tests/workers/extraction -q -k "told_by"`
Expected: FAIL — unexpected keyword / unknown field.

- [ ] **Step 3: Implement producer + message**

`src/flashback/queues/extraction.py` — add the parameter and payload key:

```python
    async def push(
        self,
        *,
        session_id: UUID,
        person_id: UUID,
        segment_turns: list[Turn],
        rolling_summary: str,
        prior_rolling_summary: str,
        seeded_question_id: UUID | None,
        candidate_question_ids: list[UUID] | None = None,
        contributor_display_name: str = "",
        told_by_user_id: str | None = None,
        is_final: bool = False,
    ) -> str:
```

and in the payload dict, after `contributor_display_name`:

```python
            "told_by_user_id": told_by_user_id or None,
```

`src/flashback/workers/extraction/schema.py` — on `ExtractionMessage`, after `contributor_display_name`:

```python
    told_by_user_id: UUID | None = None
    """Node user who spoke this segment. None = creator era (spec D2)."""
```

- [ ] **Step 4: Wire both push sites**

`detect_segment.py` (line ~112) and `wrap_session.py` (line ~135) — add alongside the existing `contributor_display_name=` argument:

```python
            told_by_user_id=wm_state.user_id or None,
```

- [ ] **Step 5: Run the touched suites**

Run: `python -m pytest tests/queues tests/workers/extraction tests/orchestrator -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/queues/extraction.py src/flashback/workers/extraction/schema.py src/flashback/orchestrator/steps/detect_segment.py src/flashback/orchestrator/steps/wrap_session.py tests
git commit -m "feat(collaborator): carry told_by_user_id on the extraction queue payload"
```

---

### Task 6: Extraction persistence stamps provenance

**Files:**
- Modify: `src/flashback/workers/extraction/persistence.py` — `persist_extraction` (line 164), `_persist_entities` (394), `_insert_traits` (654), `_insert_moment` (727), `_insert_dropped_reference_questions` (770)
- Modify: `src/flashback/workers/extraction/worker.py:338-352` (`persist_extraction` call)
- Test: `tests/workers/extraction/test_persistence.py` (or the module's persistence test file — locate with `grep -rln "persist_extraction" tests/`)

- [ ] **Step 1: Write the failing tests**

Follow the existing persistence-test pattern (fake cursor or test DB — read the test file first and mirror it). Cases:

```python
def test_moment_insert_stamps_told_by():
    # call persist_extraction with told_by_user_id="...1111", told_by_display_name="Ravi"
    # assert the INSERT INTO moments parameter tuple contains both values

def test_moment_insert_stamps_null_when_absent():
    # told_by args omitted -> INSERT params carry None, None

def test_fresh_entity_insert_stamps_told_by():
    # new entity (no name match) -> INSERT INTO entities params include the user id

def test_entity_reuse_does_not_restamp():
    # name-match path (_reuse_existing_entity) -> no told_by_user_id in any UPDATE

def test_fresh_trait_insert_stamps_told_by():
    # merge_resolutions[i] is None -> INSERT INTO traits params include the user id

def test_trait_merge_update_does_not_restamp():
    # merge_resolutions[i] set -> the UPDATE traits statement has no told_by_user_id

def test_dropped_reference_question_stamps_told_by():
    # P1 question INSERT params include the user id
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/workers/extraction -q -k "told_by or restamp"`
Expected: FAIL — unexpected keyword arguments.

- [ ] **Step 3: Implement**

`persist_extraction` gains two keyword params (after `entity_description_overrides`):

```python
    told_by_user_id: str | None = None,
    told_by_display_name: str | None = None,
```

Thread them down:
- `_persist_entities(..., told_by_user_id=told_by_user_id)`
- `_insert_traits(..., told_by_user_id=told_by_user_id)`
- `_insert_moment(..., told_by_user_id=told_by_user_id, told_by_display_name=told_by_display_name)`
- `_insert_dropped_reference_questions(..., told_by_user_id=told_by_user_id)`

`_insert_moment` — extend the SQL and params:

```python
        INSERT INTO moments
              (person_id, title, narrative, time_anchor,
               life_period_estimate, sensory_details, emotional_tone,
               contributor_perspective, generation_prompt,
               llm_provider, llm_model, prompt_version,
               told_by_user_id, told_by_display_name)
        VALUES (%s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s)
        RETURNING id::text
```

with `told_by_user_id, told_by_display_name or None` appended to the parameter tuple. (Refinement/supersession needs no extra work: `_supersede_moment` only flips the old row; the new moment goes through `_insert_moment` and gets the refining segment's stamp — spec D4.4.)

`_persist_entities` — add the param `told_by_user_id: str | None = None`; extend ONLY the `INSERT INTO entities` statement (the `_reuse_existing_entity` path is untouched — spec D4 / D3 "fresh inserts only"):

```python
            INSERT INTO entities
                  (person_id, kind, name, description, aliases,
                   attributes, generation_prompt,
                   llm_provider, llm_model, prompt_version,
                   told_by_user_id)
            VALUES (%s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s)
            RETURNING id::text
```

`_insert_traits` — add the param; extend only the `INSERT INTO traits` branch (the merge-`UPDATE` branch is untouched):

```python
            INSERT INTO traits
                  (person_id, name, description, strength,
                   llm_provider, llm_model, prompt_version,
                   told_by_user_id)
            VALUES (%s, %s, %s, 'mentioned_once',
                    %s, %s, %s,
                    %s)
            RETURNING id::text
```

`_insert_dropped_reference_questions` — add the param; extend the `INSERT INTO questions`:

```python
            INSERT INTO questions
                  (person_id, text, source, attributes,
                   llm_provider, llm_model, prompt_version,
                   told_by_user_id)
            VALUES (%s, %s, 'dropped_reference', %s,
                    %s, %s, %s,
                    %s)
            RETURNING id::text
```

`worker.py` `persist_extraction` call (line 338) — add:

```python
                        told_by_user_id=(
                            str(payload.told_by_user_id)
                            if payload.told_by_user_id
                            else None
                        ),
                        told_by_display_name=(
                            payload.contributor_display_name or None
                        ),
```

- [ ] **Step 4: Run the extraction suite**

Run: `python -m pytest tests/workers/extraction -q`
Expected: PASS, including all pre-existing tests (new params default to None).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/workers/extraction tests/workers/extraction
git commit -m "feat(collaborator): extraction persistence stamps told_by provenance"
```

---

### Task 7: `processed_extractions` bookkeeping

**Files:**
- Modify: `src/flashback/workers/extraction/idempotency.py:35-75` (`mark_processed`)
- Modify: `src/flashback/workers/extraction/worker.py:362-372` (`mark_processed` call)
- Test: the existing `mark_processed` test module (locate: `grep -rln "mark_processed" tests/`)

- [ ] **Step 1: Write the failing test**

```python
def test_mark_processed_records_told_by_user_id():
    # call mark_processed with told_by_user_id="...1111"
    # assert the INSERT INTO processed_extractions param tuple includes it

def test_mark_processed_told_by_defaults_none():
    # omit the kwarg -> None in params; NOTIFY payload unchanged
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/workers/extraction -q -k "mark_processed and told_by"`
Expected: FAIL — unexpected keyword.

- [ ] **Step 3: Implement**

`mark_processed` gains `told_by_user_id: str | None = None` (after `is_final`); the INSERT becomes:

```python
        INSERT INTO processed_extractions
              (sqs_message_id, person_id, session_id, moments_written,
               entities_written, traits_written, is_final, status,
               told_by_user_id)
        VALUES (%s, %s, %s, %s,
                %s, %s, %s, %s,
                %s)
        ON CONFLICT (sqs_message_id) DO NOTHING
        RETURNING sqs_message_id
```

with `told_by_user_id` appended to the tuple. The NOTIFY payload is **unchanged** — it carries identifiers + convenience counts only (CLAUDE.md invariant #25); consumers needing provenance read the table.

`worker.py` call site — add:

```python
                        told_by_user_id=(
                            str(payload.told_by_user_id)
                            if payload.told_by_user_id
                            else None
                        ),
```

- [ ] **Step 4: Run the suite**

Run: `python -m pytest tests/workers/extraction -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/workers/extraction tests/workers/extraction
git commit -m "feat(collaborator): record told_by_user_id on processed_extractions"
```

---

### Task 8: Producer questions (P2/P3/P5) stamp session user

**Files:**
- Modify: `src/flashback/queues/producers_per_session.py:17-22` (`push`)
- Modify: `src/flashback/orchestrator/steps/wrap_session.py:232-247` (`_push_producers_per_session`)
- Modify: `src/flashback/workers/producers/schema.py:20-27` (`ProducerMessage`)
- Modify: `src/flashback/workers/producers/worker.py:58-67` (run_once call)
- Modify: `src/flashback/workers/producers/runner.py:68-136` (`run_once` → persist)
- Modify: `src/flashback/workers/producers/persistence.py:26-38,97-113` (`persist_producer_result`, `_insert_question`)
- Test: `tests/workers/producers/`, plus the wrap-session orchestrator tests

P4 (thread detector) and the `__main__.py` cadence CLI keep NULL provenance by default — no change there beyond the new defaulted parameter.

- [ ] **Step 1: Write the failing tests**

In `tests/workers/producers/` (mirror existing fixture patterns):

```python
def test_insert_question_stamps_told_by():
    # persist_producer_result(cursor, result=..., told_by_user_id="...1111")
    # -> INSERT INTO questions params include the id

def test_insert_question_null_without_session_user():
    # told_by_user_id omitted -> None in params

def test_producer_message_parses_told_by_user_id():
    # ProducerMessage with told_by_user_id round-trips; absent -> None
```

In the wrap-session tests: assert `producers_per_session_queue.push` is called with `user_id` read from WM state.

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/workers/producers tests/orchestrator -q -k "told_by or producers"`
Expected: FAIL.

- [ ] **Step 3: Implement the chain**

`queues/producers_per_session.py`:

```python
    async def push(
        self,
        *,
        person_id: UUID,
        session_id: UUID,
        told_by_user_id: str | None = None,
    ) -> str:
        payload = {
            "person_id": str(person_id),
            "session_id": str(session_id),
            "idempotency_key": str(session_id),
            "told_by_user_id": told_by_user_id or None,
        }
```

(keep any existing payload keys — read the file; only add the new one.)

`wrap_session.py` `_push_producers_per_session` — read the user id the same way the function family reads the display name. Add a sibling helper next to `_read_contributor_display_name`:

```python
async def _read_user_id(
    state: SessionWrapState,
    deps: OrchestratorDeps,
) -> str | None:
    """Session user for provenance stamping; None if missing (creator era)."""
    try:
        wm_state = await deps.working_memory.get_state(str(state.session_id))
    except Exception:  # noqa: BLE001
        return None
    return wm_state.user_id or None
```

and pass `told_by_user_id=await _read_user_id(state, deps)` in the producers push (and in Task 9's profile-summary push).

`workers/producers/schema.py` `ProducerMessage` — add:

```python
    told_by_user_id: UUID | None = None
```

`workers/producers/worker.py` — pass `told_by_user_id=msg.body.told_by_user_id` (adapt to how the worker accesses the parsed message — read lines 40-68 first) into `run_once`.

`runner.py` `run_once` — add the keyword param `told_by_user_id: UUID | None = None` and forward it:

```python
                persist = persist_producer_result(
                    cur,
                    result=produced,
                    told_by_user_id=(
                        str(told_by_user_id) if told_by_user_id else None
                    ),
                )
```

`persistence.py`:

```python
def persist_producer_result(
    cursor, *, result: ProducerResult, told_by_user_id: str | None = None
) -> PersistResult:
```

forwarding to `_insert_question(..., told_by_user_id=told_by_user_id)`, and:

```python
def _insert_question(
    cursor,
    *,
    person_id: str,
    text: str,
    source: str,
    attributes: dict,
    told_by_user_id: str | None = None,
) -> str:
    cursor.execute(
        """
        INSERT INTO questions (person_id, text, source, attributes, told_by_user_id)
        VALUES                (%s,        %s,   %s,     %s,         %s)
        RETURNING id::text
        """,
        (person_id, text, source, Json(attributes), told_by_user_id),
    )
    return cursor.fetchone()[0]
```

- [ ] **Step 4: Run the suites**

Run: `python -m pytest tests/workers/producers tests/orchestrator -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/queues/producers_per_session.py src/flashback/orchestrator/steps/wrap_session.py src/flashback/workers/producers tests
git commit -m "feat(collaborator): per-session producers stamp told_by_user_id on questions"
```

---

### Task 9: Profile facts stamp session user; upsert endpoint accepts `user_id`

**Files:**
- Modify: `src/flashback/queues/profile_summary.py:17-31` (`push`)
- Modify: `src/flashback/orchestrator/steps/wrap_session.py:194-211` (`_push_profile_summary` — pass `told_by_user_id=await _read_user_id(state, deps)` from Task 8's helper)
- Modify: `src/flashback/workers/profile_summary/` — message schema + the call into `upsert_fact` (locate: `grep -rn "upsert_fact" src/flashback/workers/profile_summary/`)
- Modify: `src/flashback/profile_facts/queries.py:27-37` (`INSERT_FACT`)
- Modify: `src/flashback/profile_facts/repository.py:76-193` (`upsert_fact`) and the async HTTP variant below line 197
- Modify: `src/flashback/profile_facts/schema.py` (upsert request model gains `user_id: UUID | None = None`)
- Modify: `src/flashback/http/routes/profile_facts.py` (pass `body.user_id` through)
- Test: `tests/` profile-facts + profile-summary modules (locate: `grep -rln "upsert_fact" tests/`)

- [ ] **Step 1: Write the failing tests**

```python
def test_insert_fact_stamps_told_by():
    # upsert_fact(..., told_by_user_id="...1111") -> INSERT params include it

def test_insert_fact_null_without_user():
    # omitted -> None

def test_upsert_route_accepts_user_id():
    # POST /profile_facts/upsert with user_id -> row stamped (route test
    # pattern: mirror the existing upsert route tests)

def test_profile_summary_queue_payload_carries_user_id():
    # ProfileSummaryQueueProducer.push(told_by_user_id=...) -> payload key
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests -q -k "profile_fact and told_by or (profile_summary and user_id)"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`queues/profile_summary.py` — add `told_by_user_id: str | None = None` param and `"told_by_user_id": told_by_user_id or None` payload key.

`INSERT_FACT` in `queries.py`:

```sql
INSERT INTO profile_facts (
    id, person_id, fact_key, question_text, answer_text, source, status,
    llm_provider, llm_model, prompt_version, told_by_user_id
) VALUES (
    %(id)s, %(person_id)s, %(fact_key)s,
    %(question_text)s, %(answer_text)s, %(source)s, 'active',
    %(llm_provider)s, %(llm_model)s, %(prompt_version)s, %(told_by_user_id)s
)
RETURNING id
```

`repository.py` — both `upsert_fact` and the async HTTP variant gain `told_by_user_id: str | None = None` and pass `"told_by_user_id": told_by_user_id` in the `INSERT_FACT` params dict. (The supersede-UPDATE is untouched — each new active row carries its own author.)

Profile-summary worker — its message schema gains `told_by_user_id: UUID | None = None`; the facts-writing call passes it into `upsert_fact` as a string (`str(...) if ... else None`). Read the worker's runner/context modules first to find the exact call site (`grep -rn "upsert_fact" src/flashback/workers/profile_summary/`).

Upsert request model in `profile_facts/schema.py` gains `user_id: UUID | None = None`; the route passes `told_by_user_id=str(body.user_id) if body.user_id else None`.

- [ ] **Step 4: Run the suites**

Run: `python -m pytest tests -q -k "profile"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/queues/profile_summary.py src/flashback/orchestrator/steps/wrap_session.py src/flashback/workers/profile_summary src/flashback/profile_facts src/flashback/http/routes/profile_facts.py tests
git commit -m "feat(collaborator): profile facts stamp told_by_user_id"
```

---

### Task 10: Documentation

**Files:**
- Modify: `API.md` (request bodies at lines ~260, ~344, ~425: replace `role_id` with `user_id`, note the deprecated tolerated field; document `user_id` on the profile-facts upsert body)
- Modify: `NODE_INTEGRATION.md` (Node must send `user_id` on session/turn/stream; `role_id` retired; provenance columns Node may read)
- Modify: `SCHEMA.md` (the six `told_by_user_id` columns + `moments.told_by_display_name` + the partial index, with the D3 semantics table)
- Modify: `CLAUDE.md` §9 (replace `role_id` in the endpoint contract) and add a §4 invariant **#26** stating: every contributor-authored row carries `told_by_user_id`; NULL = creator era; only `moments.told_by_user_id` ever drives hiding/removal; the LLM never sees or emits provenance.

- [ ] **Step 1: Update all four docs** as listed. Keep wording consistent with the spec's D1–D4.

- [ ] **Step 2: Verify no stale `role_id` references**

Run: `grep -rn "role_id" API.md NODE_INTEGRATION.md CLAUDE.md SCHEMA.md`
Expected: only the deliberate "deprecated/retired" mentions.

- [ ] **Step 3: Commit**

```bash
git add API.md NODE_INTEGRATION.md SCHEMA.md CLAUDE.md
git commit -m "docs(collaborator): user_id contract + told_by provenance invariant"
```

---

### Task 11: Final verification sweep

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: PASS, zero failures.

- [ ] **Step 2: Provenance grep audit**

Run: `grep -rn "told_by" src/ | grep -v test`
Expected: stamping sites only in: extraction persistence (4 insert fns), idempotency, producers persistence/runner/schema/worker, profile_facts queries/repository/schema/route, profile-summary worker + queue, extraction queue/schema, detect_segment, wrap_session. No read-path file (retrieval, response generator, phase gate) may reference `told_by` — this sub-project is write-only (spec "Out of scope").

Run: `grep -rn "role_id" src/`
Expected: only the two deprecated request-model fields.

- [ ] **Step 3: Commit any stragglers and report**

Use superpowers:verification-before-completion before claiming done.
