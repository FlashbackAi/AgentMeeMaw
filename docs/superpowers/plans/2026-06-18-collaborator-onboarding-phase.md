# Collaborator Onboarding Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a collaborator a lightweight 2-item onboarding phase (mirroring the creator's starter phase) — an indirect "defining memory" nudge, agent-derived relationship capture, and a sticky `phase` flag on `collaborator_onboarding` that flips `onboarding → active` once both items are satisfied.

**Architecture:** A new sticky `phase` column on `collaborator_onboarding` (migration 0032). Satisfaction is derived from existing columns: **connection** = voice anchor present (form-mirrored *or* agent-inferred) or modal resolved; **memory** = first collaborator moment recorded. The Extraction Worker flips `first_moment_id`, writes an inferred relationship into `voice_anchor_text` (non-clobber), and runs the Onboarding Check (a guarded atomic UPDATE) — all in its existing transaction. A new `/turn` step emits an indirect memory tap card once per session until graduated.

**Tech Stack:** Python, psycopg (async pool in orchestrator, sync cursor in workers), Pydantic, Postgres + JSONB, Valkey/Redis working memory, pytest (`asyncio_mode=auto`).

## Global Constraints

- **NO COMMITS THIS CYCLE.** All work lands in the working tree on `feature/collaborator-provenance`; the user commits. **Skip every `git add`/`git commit` step** — each task ends with `git status --short` instead.
- **Test command (no-DB):** `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings`
- **Test command (DB-gated):** `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings` (Postgres is up: docker `flashback-postgres`, db `flashback_test`, role `flashback`). DB-gated tests skip when `TEST_DATABASE_URL` is unset.
- **Judge regressions by diffing the FAILED list against baseline**, not absolute counts (the branch carries pre-existing unrelated failures).
- `collaborator_onboarding` is an agent-internal mirror; the new `phase` flag is distinct from Node's DynamoDB `onboarding_complete`.
- The agent never *asks* the relationship directly; it is inferred from the contributor's words (D8) or mirrored from the Node form.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `migrations/0032_collaborator_onboarding_phase.{up,down}.sql` (new) | `phase` + `phase_locked_at` columns + view recreate | 1 |
| `src/flashback/collaborator_onboarding/queries.py` | new SQL constants (read state, mark first moment, set anchor if empty, flip phase, increment taps) | 2 |
| `src/flashback/collaborator_onboarding/repository.py` | async helpers wrapping those SQL | 2 |
| `src/flashback/working_memory/schema.py` + `client.py` | per-session `collaborator_onboarding_tap_emitted` flag | 3 |
| `src/flashback/orchestrator/tap_options.py` | `generate_onboarding_tap` (indirect prompt + chips) | 4 |
| `src/flashback/workers/extraction/schema.py` + `prompts.py` | optional `contributor_relationship` field (D8) | 5 |
| `src/flashback/workers/extraction/worker.py` | tx-tail wiring: first-moment flip + anchor write + Onboarding Check | 6 |
| `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py` | run Onboarding Check after upsert | 7 |
| `src/flashback/orchestrator/steps/select_collaborator_onboarding_tap.py` (new) | the nudge step | 8 |
| `src/flashback/orchestrator/steps/select_coverage_tap.py` | early-return if `state.taps` already set | 8 |
| `src/flashback/orchestrator/orchestrator.py` | wire the nudge step before `select_coverage_tap` (JSON + stream) | 8 |
| `CLAUDE.md` | document the collaborator onboarding phase | 9 |

---

## Task 1: Migration 0032 — `phase` flag

**Files:**
- Create: `migrations/0032_collaborator_onboarding_phase.up.sql`
- Create: `migrations/0032_collaborator_onboarding_phase.down.sql`

**Interfaces:**
- Produces: `collaborator_onboarding.phase TEXT NOT NULL DEFAULT 'onboarding'` (`CHECK IN ('onboarding','active')`), `phase_locked_at TIMESTAMPTZ`; `active_collaborator_onboarding` view exposes both.

- [ ] **Step 1: Write the up migration**

`migrations/0032_collaborator_onboarding_phase.up.sql`:

```sql
ALTER TABLE collaborator_onboarding
    ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'onboarding'
        CHECK (phase IN ('onboarding', 'active'));

ALTER TABLE collaborator_onboarding
    ADD COLUMN IF NOT EXISTS phase_locked_at TIMESTAMPTZ;

-- Recreate the SELECT * view so it picks up the new columns.
CREATE OR REPLACE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';
```

- [ ] **Step 2: Write the down migration**

`migrations/0032_collaborator_onboarding_phase.down.sql`:

```sql
DROP VIEW IF EXISTS active_collaborator_onboarding;

ALTER TABLE collaborator_onboarding
    DROP COLUMN IF EXISTS phase_locked_at;

ALTER TABLE collaborator_onboarding
    DROP COLUMN IF EXISTS phase;

CREATE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';
```

- [ ] **Step 3: Apply + verify round-trip against `flashback_test`**

Apply up, confirm columns + view; run down, confirm it succeeds despite the view dependency; re-apply up to leave the column present for later tasks. Commands:

```bash
docker exec -i flashback-postgres psql -U flashback -d flashback_test -f - < migrations/0032_collaborator_onboarding_phase.up.sql
docker exec -i flashback-postgres psql -U flashback -d flashback_test -c "SELECT phase, phase_locked_at FROM active_collaborator_onboarding LIMIT 0;"
docker exec -i flashback-postgres psql -U flashback -d flashback_test -f - < migrations/0032_collaborator_onboarding_phase.down.sql
docker exec -i flashback-postgres psql -U flashback -d flashback_test -f - < migrations/0032_collaborator_onboarding_phase.up.sql
```

Expected: the `SELECT ... LIMIT 0` succeeds (columns exist); down runs with no error; final up restores them. Report each command's result.

- [ ] **Step 4: Verify working tree** — `git status --short` (do not commit).

---

## Task 2: Onboarding SQL + repository helpers

**Files:**
- Modify: `src/flashback/collaborator_onboarding/queries.py`
- Modify: `src/flashback/collaborator_onboarding/repository.py`
- Test: `tests/collaborator_onboarding/test_phase_helpers.py` (new, DB-gated)

**Interfaces:**
- Consumes: the columns from Task 1.
- Produces (used by Tasks 6, 7, 8):
  - SQL constants `GET_ONBOARDING_STATE_SQL`, `MARK_FIRST_MOMENT_SQL`, `SET_VOICE_ANCHOR_IF_EMPTY_SQL`, `FLIP_PHASE_IF_COMPLETE_SQL`, `INCREMENT_TAPS_EMITTED_SQL` (usable with a sync cursor in workers OR async conn in orchestrator).
  - `OnboardingState` dataclass: `phase: str`, `has_memory: bool`, `has_connection: bool`, `taps_emitted: int`.
  - `async get_onboarding_state(conn, *, person_id, user_id) -> OnboardingState | None`
  - `async flip_phase_if_complete(conn, *, person_id, user_id) -> None`
  - `async increment_taps_emitted(conn, *, person_id, user_id) -> None`

- [ ] **Step 1: Write the failing DB-gated test**

First read `tests/collaborator_onboarding/test_repository.py` and any conftest in that dir to reuse the exact DB fixtures (connection fixture + a person-insert helper). Then create `tests/collaborator_onboarding/test_phase_helpers.py`:

```python
import uuid
import pytest
from flashback.collaborator_onboarding.repository import (
    OnboardingState,
    get_onboarding_state,
    flip_phase_if_complete,
    increment_taps_emitted,
    upsert_onboarding,
)
from flashback.collaborator_onboarding.queries import MARK_FIRST_MOMENT_SQL

pytestmark = pytest.mark.asyncio


async def _insert_moment(conn, person_id, user_id):
    mid = uuid.uuid4()
    await conn.execute(
        """INSERT INTO moments (id, person_id, title, narrative, told_by_user_id)
           VALUES (%s, %s, 'm', 'n', %s)""",
        (str(mid), str(person_id), str(user_id)),
    )
    return mid


async def test_get_state_none_when_no_row(db_conn, make_person):
    person_id = await make_person(db_conn)
    st = await get_onboarding_state(db_conn, person_id=person_id, user_id=uuid.uuid4())
    assert st is None


async def test_flip_requires_both_items(db_conn, make_person):
    person_id = await make_person(db_conn)
    user_id = uuid.uuid4()
    # Connection only (voice anchor) -> stays onboarding.
    await upsert_onboarding(db_conn, person_id=person_id, user_id=user_id,
                            voice_anchor_text="his daughter", voice_anchored_at=None)
    await flip_phase_if_complete(db_conn, person_id=person_id, user_id=user_id)
    st = await get_onboarding_state(db_conn, person_id=person_id, user_id=user_id)
    assert st.phase == "onboarding"
    assert st.has_connection is True and st.has_memory is False

    # Add the memory -> now flips.
    mid = await _insert_moment(db_conn, person_id, user_id)
    await db_conn.execute(
        MARK_FIRST_MOMENT_SQL,
        {"person_id": person_id, "user_id": user_id, "moment_id": str(mid)},
    )
    await flip_phase_if_complete(db_conn, person_id=person_id, user_id=user_id)
    st = await get_onboarding_state(db_conn, person_id=person_id, user_id=user_id)
    assert st.phase == "active"


async def test_increment_taps(db_conn, make_person):
    person_id = await make_person(db_conn)
    user_id = uuid.uuid4()
    await upsert_onboarding(db_conn, person_id=person_id, user_id=user_id)
    await increment_taps_emitted(db_conn, person_id=person_id, user_id=user_id)
    await increment_taps_emitted(db_conn, person_id=person_id, user_id=user_id)
    st = await get_onboarding_state(db_conn, person_id=person_id, user_id=user_id)
    assert st.taps_emitted == 2
```

> Adapt `db_conn` / `make_person` to the real fixture names in `tests/collaborator_onboarding/`. `upsert_onboarding` already exists; if its CHECK rejects `voice_anchor_text` without `voice_anchored_at`, pass `voice_anchored_at=datetime.now(timezone.utc)`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/collaborator_onboarding/test_phase_helpers.py -q -p no:warnings`
Expected: FAIL — `get_onboarding_state` / `OnboardingState` / new SQL not importable.

- [ ] **Step 3: Add SQL constants**

Append to `src/flashback/collaborator_onboarding/queries.py`:

```python
GET_ONBOARDING_STATE_SQL = """
SELECT phase,
       (first_moment_id IS NOT NULL) AS has_memory,
       (voice_anchor_text IS NOT NULL
        OR modal_answered_at IS NOT NULL
        OR modal_dismissed_at IS NOT NULL) AS has_connection,
       taps_emitted
FROM collaborator_onboarding
WHERE person_id = %(person_id)s
  AND user_id   = %(user_id)s
  AND status    = 'active'
"""

MARK_FIRST_MOMENT_SQL = """
UPDATE collaborator_onboarding
   SET first_moment_id          = %(moment_id)s,
       first_moment_recorded_at = now()
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
   AND first_moment_id IS NULL
"""

SET_VOICE_ANCHOR_IF_EMPTY_SQL = """
UPDATE collaborator_onboarding
   SET voice_anchor_text = %(voice_anchor_text)s,
       voice_anchored_at = now()
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
   AND voice_anchor_text IS NULL
"""

FLIP_PHASE_IF_COMPLETE_SQL = """
UPDATE collaborator_onboarding
   SET phase = 'active', phase_locked_at = now()
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
   AND phase     = 'onboarding'
   AND first_moment_id IS NOT NULL
   AND (voice_anchor_text IS NOT NULL
        OR modal_answered_at IS NOT NULL
        OR modal_dismissed_at IS NOT NULL)
"""

INCREMENT_TAPS_EMITTED_SQL = """
UPDATE collaborator_onboarding
   SET taps_emitted = taps_emitted + 1
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
"""
```

- [ ] **Step 4: Add the async helpers**

Append to `src/flashback/collaborator_onboarding/repository.py` (add `from dataclasses import dataclass` and the new query imports at top):

```python
from dataclasses import dataclass

from flashback.collaborator_onboarding.queries import (
    FLIP_PHASE_IF_COMPLETE_SQL,
    GET_ONBOARDING_STATE_SQL,
    INCREMENT_TAPS_EMITTED_SQL,
)


@dataclass(frozen=True)
class OnboardingState:
    phase: str
    has_memory: bool
    has_connection: bool
    taps_emitted: int


async def get_onboarding_state(
    conn, *, person_id: UUID, user_id: UUID
) -> OnboardingState | None:
    cur = await conn.execute(
        GET_ONBOARDING_STATE_SQL, {"person_id": person_id, "user_id": user_id}
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return OnboardingState(
        phase=str(row[0]),
        has_memory=bool(row[1]),
        has_connection=bool(row[2]),
        taps_emitted=int(row[3]),
    )


async def flip_phase_if_complete(conn, *, person_id: UUID, user_id: UUID) -> None:
    """Onboarding Check: flip onboarding->active when both items satisfied.

    Guarded by ``phase='onboarding'`` so it is sticky and never double-stamps.
    """
    await conn.execute(
        FLIP_PHASE_IF_COMPLETE_SQL, {"person_id": person_id, "user_id": user_id}
    )


async def increment_taps_emitted(conn, *, person_id: UUID, user_id: UUID) -> None:
    await conn.execute(
        INCREMENT_TAPS_EMITTED_SQL, {"person_id": person_id, "user_id": user_id}
    )
```

- [ ] **Step 5: Run the test** (apply migration 0032 first if not already)

Run: `.venv/Scripts/python.exe -m pytest tests/collaborator_onboarding/test_phase_helpers.py -q -p no:warnings`
Expected: PASS (3 tests).

- [ ] **Step 6: Onboarding-module regression** — `.venv/Scripts/python.exe -m pytest tests/collaborator_onboarding -q -p no:warnings` (no new failures).
- [ ] **Step 7: Verify working tree** (`git status --short`).

---

## Task 3: Working Memory `collaborator_onboarding_tap_emitted` flag

**Files:**
- Modify: `src/flashback/working_memory/schema.py`
- Test: `tests/working_memory/test_onboarding_tap_flag.py` (new)

**Interfaces:**
- Produces: `WorkingMemoryState.collaborator_onboarding_tap_emitted: bool = False`, settable via `update_signals(session_id, collaborator_onboarding_tap_emitted=True)` and read via `get_state(...).collaborator_onboarding_tap_emitted`.

- [ ] **Step 1: Write the failing test**

First read `src/flashback/working_memory/schema.py` to see how booleans are parsed (look for a `_BOOL_FIELDS` / `_INT_FIELDS` set and `parse_state_hash` / `serialise_state_for_init`). Create `tests/working_memory/test_onboarding_tap_flag.py`:

```python
from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)
from datetime import datetime, timezone


def test_flag_defaults_false():
    s = WorkingMemoryState(person_id="p", started_at=datetime.now(timezone.utc))
    assert s.collaborator_onboarding_tap_emitted is False


def test_flag_round_trips_through_hash():
    # The serialised init dict carries the default; a "true" hash parses back True.
    base = serialise_state_for_init(person_id="p", user_id="u",
                                    started_at=datetime.now(timezone.utc))
    assert "collaborator_onboarding_tap_emitted" in base
    hydrated = parse_state_hash({**base, "collaborator_onboarding_tap_emitted": "True"})
    assert hydrated.collaborator_onboarding_tap_emitted is True
```

> If `serialise_state_for_init` has a different signature, match the real one (read the file). The intent: the field is in the init dict and parses back as a bool.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/working_memory/test_onboarding_tap_flag.py -q -p no:warnings`
Expected: FAIL — field missing.

- [ ] **Step 3: Add the field**

In `src/flashback/working_memory/schema.py`, add to `WorkingMemoryState` (near `signal_pending_tap_question`):

```python
    collaborator_onboarding_tap_emitted: bool = False
```

If the module has a `_BOOL_FIELDS` set used by `parse_state_hash` to coerce `"True"/"False"` strings, add `"collaborator_onboarding_tap_emitted"` to it. If booleans are parsed generically (pydantic coerces `"True"`), no extra change. Ensure `serialise_state_for_init` includes the field at its default (if it builds the dict explicitly, add `"collaborator_onboarding_tap_emitted": "False"`; if it dumps the model, it's automatic).

- [ ] **Step 4: Run the test** — Expected: PASS (2).
- [ ] **Step 5: WM regression** — `.venv/Scripts/python.exe -m pytest tests/working_memory -q -p no:warnings` (no new failures).
- [ ] **Step 6: Verify working tree.**

---

## Task 4: `generate_onboarding_tap` (indirect prompt + chips)

**Files:**
- Modify: `src/flashback/orchestrator/tap_options.py`
- Test: `tests/orchestrator/test_onboarding_tap_options.py` (new)

**Interfaces:**
- Consumes: existing `generate_tap_options`.
- Produces: `async generate_onboarding_tap(*, settings, person_name: str, relationship: str | None) -> tuple[str, list[str]]` — returns `(prompt_text, options)`; options `[]` on LLM failure.

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/test_onboarding_tap_options.py`:

```python
import pytest
from flashback.orchestrator import tap_options

pytestmark = pytest.mark.asyncio


async def test_onboarding_prompt_is_indirect_and_names_subject(monkeypatch):
    async def _fake_options(**kwargs):
        return ["Her quick smile", "Sunday mornings", "Always cooking", "On the porch"]
    monkeypatch.setattr(tap_options, "generate_tap_options", _fake_options)

    text, options = await tap_options.generate_onboarding_tap(
        settings=object(), person_name="David", relationship="his daughter",
    )
    assert "David" in text
    # Indirect: never a direct relationship/meaning question.
    lowered = text.lower()
    assert "what did" not in lowered and "mean to you" not in lowered
    assert "relationship" not in lowered
    assert options == ["Her quick smile", "Sunday mornings", "Always cooking", "On the porch"]


async def test_onboarding_options_fall_back_to_empty(monkeypatch):
    async def _fail(**kwargs):
        return []
    monkeypatch.setattr(tap_options, "generate_tap_options", _fail)
    text, options = await tap_options.generate_onboarding_tap(
        settings=object(), person_name="David", relationship=None,
    )
    assert text and options == []
```

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (`generate_onboarding_tap` undefined).

- [ ] **Step 3: Implement**

Append to `src/flashback/orchestrator/tap_options.py`:

```python
def _onboarding_prompt(person_name: str) -> str:
    name = person_name or "them"
    return (
        f"When you picture {name}, what's one small, ordinary moment with "
        f"them that's stayed with you?"
    )


async def generate_onboarding_tap(
    *,
    settings,
    person_name: str,
    relationship: str | None,
) -> tuple[str, list[str]]:
    """Indirect 'defining memory' onboarding prompt + 4 chips.

    The prompt is templated (warm, never a direct 'what did they mean to
    you?'); the chips reuse :func:`generate_tap_options`. Options are ``[]``
    on any failure — the card falls back to prompt + free-text.
    """
    text = _onboarding_prompt(person_name)
    options = await generate_tap_options(
        settings=settings,
        question_text=text,
        person_name=person_name,
        person_relationship=relationship,
        dimension="",
    )
    return text, options
```

- [ ] **Step 4: Run the test** — Expected: PASS (2).
- [ ] **Step 5: Verify working tree.**

---

## Task 5: Extraction `contributor_relationship` field (D8)

**Files:**
- Modify: `src/flashback/workers/extraction/schema.py` (`ExtractionResult`)
- Modify: `src/flashback/workers/extraction/prompts.py` (`EXTRACTION_TOOL` + system prompt)
- Test: `tests/workers/extraction/test_contributor_relationship.py` (new)

**Interfaces:**
- Produces: `ExtractionResult.contributor_relationship: str | None = None` (the contributor's relationship to the subject, inferred; optional).

- [ ] **Step 1: Write the failing test**

Create `tests/workers/extraction/test_contributor_relationship.py`:

```python
from flashback.workers.extraction.schema import ExtractionResult


def test_contributor_relationship_defaults_none():
    r = ExtractionResult(moments=[], entities=[], traits=[],
                         dropped_references=[], extraction_notes="")
    assert r.contributor_relationship is None


def test_contributor_relationship_accepts_value():
    r = ExtractionResult(moments=[], entities=[], traits=[],
                         dropped_references=[], extraction_notes="",
                         contributor_relationship="his daughter")
    assert r.contributor_relationship == "his daughter"
```

> Match `ExtractionResult`'s real required fields when constructing it (read `schema.py` lines ~106-177). If some are optional with defaults, drop them from the constructor.

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (no such field).

- [ ] **Step 3: Add the field to the schema**

In `src/flashback/workers/extraction/schema.py`, add to `ExtractionResult`:

```python
    contributor_relationship: str | None = None
```

- [ ] **Step 4: Add the tool property + prompt rubric**

In `src/flashback/workers/extraction/prompts.py`, add to `EXTRACTION_TOOL`'s top-level `properties` (NOT to `required`):

```python
            "contributor_relationship": {
                "type": "string",
                "description": (
                    "If the person speaking is clearly someone OTHER than the "
                    "subject describing their own bond to the subject (e.g. 'my "
                    "dad', 'we served together'), a SHORT relationship phrase "
                    "from the subject's side: 'his daughter', 'her colleague', "
                    "'his old friend'. Omit if not evident. Never invent it."
                ),
            },
```

Append to `EXTRACTION_SYSTEM_PROMPT` a short instruction:

```
If the contributor reveals how they themselves are related to the subject,
set `contributor_relationship` to a brief phrase from the subject's side
(e.g. "his daughter"). Infer only from what they actually say; omit otherwise.
```

- [ ] **Step 5: Run the test** — Expected: PASS (2).
- [ ] **Step 6: Extraction regression** — `.venv/Scripts/python.exe -m pytest tests/workers/extraction -q -p no:warnings` (no new failures; update any test asserting the exact `ExtractionResult` shape to allow the new optional field).
- [ ] **Step 7: Verify working tree.**

---

## Task 6: Extraction Worker tx-tail wiring

**Files:**
- Modify: `src/flashback/workers/extraction/worker.py`
- Test: `tests/workers/extraction/test_onboarding_graduation.py` (new, DB-gated)

**Interfaces:**
- Consumes: `MARK_FIRST_MOMENT_SQL`, `SET_VOICE_ANCHOR_IF_EMPTY_SQL`, `FLIP_PHASE_IF_COMPLETE_SQL` (Task 2); `ExtractionResult.contributor_relationship` (Task 5); `persistence_result.moment_ids`; `payload.told_by_user_id`; `payload.person_id`; `extraction` (the `ExtractionResult`).

- [ ] **Step 1: Write the failing DB-gated test**

Create `tests/workers/extraction/test_onboarding_graduation.py`. It calls a new helper `apply_collaborator_onboarding_extraction(cur, person_id, user_id, moment_ids, contributor_relationship)` directly (so we don't need the whole worker), seeded with an active onboarding row:

```python
import uuid
import pytest
from flashback.workers.extraction.worker import apply_collaborator_onboarding_extraction
from flashback.collaborator_onboarding.queries import GET_ONBOARDING_STATE_SQL

# These tests are SYNCHRONOUS — they drive the sync `apply_collaborator_onboarding_extraction`
# helper with a sync DB cursor. Do NOT mark them asyncio.


def _seed_row(cur, person_id, user_id, voice_anchor=None):
    cur.execute(
        """INSERT INTO collaborator_onboarding (person_id, user_id, voice_anchor_text, voice_anchored_at)
           VALUES (%s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE now() END)""",
        (str(person_id), str(user_id), voice_anchor, voice_anchor),
    )


def test_memory_plus_inferred_relationship_graduates(db_cursor, make_person_sync):
    person_id = make_person_sync(db_cursor)
    user_id = uuid.uuid4()
    _seed_row(db_cursor, person_id, user_id, voice_anchor=None)  # no connection yet
    mid = uuid.uuid4()
    db_cursor.execute(
        "INSERT INTO moments (id, person_id, title, narrative, told_by_user_id) VALUES (%s,%s,'m','n',%s)",
        (str(mid), str(person_id), str(user_id)),
    )
    apply_collaborator_onboarding_extraction(
        db_cursor, person_id=str(person_id), user_id=str(user_id),
        moment_ids=[str(mid)], contributor_relationship="his daughter",
    )
    db_cursor.execute(GET_ONBOARDING_STATE_SQL, {"person_id": person_id, "user_id": user_id})
    phase, has_memory, has_connection, _ = db_cursor.fetchone()
    assert phase == "active" and has_memory and has_connection


def test_no_clobber_existing_anchor(db_cursor, make_person_sync):
    person_id = make_person_sync(db_cursor)
    user_id = uuid.uuid4()
    _seed_row(db_cursor, person_id, user_id, voice_anchor="his daughter")
    mid = uuid.uuid4()
    db_cursor.execute(
        "INSERT INTO moments (id, person_id, title, narrative, told_by_user_id) VALUES (%s,%s,'m','n',%s)",
        (str(mid), str(person_id), str(user_id)),
    )
    apply_collaborator_onboarding_extraction(
        db_cursor, person_id=str(person_id), user_id=str(user_id),
        moment_ids=[str(mid)], contributor_relationship="some other phrase",
    )
    db_cursor.execute(
        "SELECT voice_anchor_text FROM collaborator_onboarding WHERE person_id=%s AND user_id=%s",
        (str(person_id), str(user_id)),
    )
    assert db_cursor.fetchone()[0] == "his daughter"  # unchanged


def test_creator_era_null_user_is_noop(db_cursor, make_person_sync):
    person_id = make_person_sync(db_cursor)
    # No onboarding row; user_id None -> the helper must early-return without error.
    apply_collaborator_onboarding_extraction(
        db_cursor, person_id=str(person_id), user_id=None,
        moment_ids=[str(uuid.uuid4())], contributor_relationship=None,
    )
```

> Reuse the sync DB cursor fixture pattern from existing extraction persistence tests (`tests/workers/extraction/test_persistence_provenance.py`). Adapt `db_cursor` / `make_person_sync` to the real fixtures.

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (`apply_collaborator_onboarding_extraction` undefined).

- [ ] **Step 3: Add the helper to `worker.py`**

In `src/flashback/workers/extraction/worker.py`, add imports and a module-level helper:

```python
from flashback.collaborator_onboarding.queries import (
    FLIP_PHASE_IF_COMPLETE_SQL,
    MARK_FIRST_MOMENT_SQL,
    SET_VOICE_ANCHOR_IF_EMPTY_SQL,
)


def apply_collaborator_onboarding_extraction(
    cur,
    *,
    person_id: str,
    user_id: str | None,
    moment_ids: list[str],
    contributor_relationship: str | None,
) -> None:
    """Collaborator onboarding tx-tail: mark first moment, fill the voice
    anchor from the inferred relationship (non-clobber), then run the
    Onboarding Check. All no-ops when there is no active onboarding row
    (e.g. creator-era NULL user, or a creator with no row)."""
    if not user_id:
        return
    if contributor_relationship and contributor_relationship.strip():
        cur.execute(
            SET_VOICE_ANCHOR_IF_EMPTY_SQL,
            {
                "person_id": person_id,
                "user_id": user_id,
                "voice_anchor_text": contributor_relationship.strip(),
            },
        )
    if moment_ids:
        cur.execute(
            MARK_FIRST_MOMENT_SQL,
            {"person_id": person_id, "user_id": user_id, "moment_id": moment_ids[0]},
        )
    cur.execute(
        FLIP_PHASE_IF_COMPLETE_SQL,
        {"person_id": person_id, "user_id": user_id},
    )
```

- [ ] **Step 4: Call it in the transaction tail**

In `_extract_and_persist` (the tx block, after `run_handover_check(...)` / before or after `mark_processed(...)` — inside the same `with conn.transaction()` cursor `cur`), add:

```python
                    apply_collaborator_onboarding_extraction(
                        cur,
                        person_id=str(payload.person_id),
                        user_id=(
                            str(payload.told_by_user_id)
                            if payload.told_by_user_id
                            else None
                        ),
                        moment_ids=persistence_result.moment_ids,
                        contributor_relationship=extraction.contributor_relationship,
                    )
```

(Place it alongside `run_coverage_tracker` / `run_handover_check` so it shares the committed transaction.)

- [ ] **Step 5: Run the test** — Expected: PASS (3).
- [ ] **Step 6: Extraction regression** — `.venv/Scripts/python.exe -m pytest tests/workers/extraction -q -p no:warnings` (no new failures).
- [ ] **Step 7: Verify working tree.**

---

## Task 7: Onboarding Check at session start

**Files:**
- Modify: `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py`
- Test: `tests/orchestrator/test_apply_collaborator_onboarding.py` (extend)

**Interfaces:**
- Consumes: `flip_phase_if_complete` (Task 2).

- [ ] **Step 1: Write the failing test**

Read the existing `tests/orchestrator/test_apply_collaborator_onboarding.py` (it uses `_FakeConn`/`_FakePool`/`_Deps`/`_state`). Add a test asserting that after the upsert, `flip_phase_if_complete` is invoked (the fake conn should record an execute carrying `phase = 'active'` / the `FLIP_PHASE_IF_COMPLETE_SQL`). Concretely, assert the SQL list captured by `_FakeConn` includes the flip query for a collaborator state:

```python
async def test_apply_runs_onboarding_check(monkeypatch):
    conn = _FakeConn()
    state = _state(uuid4(), {"role": "collaborator", "voice_anchor_text": "his daughter"})
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    executed = " ".join(sql for sql, _ in conn.calls)
    assert "phase = 'active'" in executed  # FLIP_PHASE_IF_COMPLETE_SQL ran
```

> Match `_FakeConn`'s captured-call shape (the existing tests already inspect `conn.calls`). If it stores `(sql, params)` tuples, the above works; adjust accessor if different.

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (flip not called).

- [ ] **Step 3: Call the check after the upsert**

In `apply_collaborator_onboarding`, import `flip_phase_if_complete` and call it inside the same `async with deps.db_pool.connection() as conn:` block, after `upsert_onboarding(...)` and before `await conn.commit()`:

```python
from flashback.collaborator_onboarding import flip_phase_if_complete
# ...
                await upsert_onboarding(conn, ...)  # existing call
                await flip_phase_if_complete(
                    conn, person_id=state.person_id, user_id=state.user_id
                )
                await conn.commit()
```

Ensure `flip_phase_if_complete` (and any other new repository names) are re-exported from `src/flashback/collaborator_onboarding/__init__.py` alongside the existing `upsert_onboarding` export.

- [ ] **Step 4: Run the test** — Expected: PASS.
- [ ] **Step 5: Regression** — `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_apply_collaborator_onboarding.py tests/collaborator_onboarding -q -p no:warnings` (no new failures).
- [ ] **Step 6: Verify working tree.**

---

## Task 8: `select_collaborator_onboarding_tap` step + wiring

**Files:**
- Create: `src/flashback/orchestrator/steps/select_collaborator_onboarding_tap.py`
- Modify: `src/flashback/orchestrator/steps/select_coverage_tap.py` (early-return)
- Modify: `src/flashback/orchestrator/steps/__init__.py` (export)
- Modify: `src/flashback/orchestrator/orchestrator.py` (wire before `select_coverage_tap`, JSON + stream)
- Test: `tests/orchestrator/test_select_collaborator_onboarding_tap.py` (new)

**Interfaces:**
- Consumes: `get_onboarding_state`, `increment_taps_emitted` (Task 2); `generate_onboarding_tap` (Task 4); `WorkingMemoryState.collaborator_onboarding_tap_emitted` (Task 3); `Tap` (`flashback.orchestrator.protocol`); `READ_PERSON_NAME_AND_GENDER`, `get_voice_anchor`.

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/test_select_collaborator_onboarding_tap.py` using fakes (no DB):

```python
import uuid
import pytest
from types import SimpleNamespace
from flashback.orchestrator.steps.select_collaborator_onboarding_tap import (
    select_collaborator_onboarding_tap,
)
from flashback.collaborator_onboarding.repository import OnboardingState

pytestmark = pytest.mark.asyncio


def _make(monkeypatch, *, state_obj, tap_flag=False):
    # Patch the DB-touching helpers used by the step.
    import flashback.orchestrator.steps.select_collaborator_onboarding_tap as mod
    async def _get_state(conn, **kw): return state_obj
    async def _inc(conn, **kw): return None
    async def _anchor(conn, **kw): return "his daughter"
    async def _name(deps, pid): return ("David", None)
    async def _onboarding_tap(**kw): return ("When you picture David, what's one moment…", ["a","b","c","d"])
    monkeypatch.setattr(mod, "get_onboarding_state", _get_state)
    monkeypatch.setattr(mod, "increment_taps_emitted", _inc)
    monkeypatch.setattr(mod, "get_voice_anchor", _anchor)
    monkeypatch.setattr(mod, "_read_name", _name)
    monkeypatch.setattr(mod, "generate_onboarding_tap", _onboarding_tap)


class _WM:
    def __init__(self, flag): self._flag = flag; self.signals = {}; self.tap_calls = 0
    async def get_state(self, sid): return SimpleNamespace(collaborator_onboarding_tap_emitted=self._flag)
    async def record_tap_emitted(self, **kw): self.tap_calls += 1
    async def update_signals(self, session_id, **kw): self.signals.update(kw)


def _state(user_id, wm):
    return SimpleNamespace(
        user_id=user_id, person_id=uuid.uuid4(), session_id=uuid.uuid4(),
        person_relationship=None, taps=[], working_memory_state=None,
    ), SimpleNamespace(db_pool=_Pool(), working_memory=wm, settings=object())


class _Pool:
    def connection(self):
        class _C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def commit(self): pass
        return _C()


async def test_emits_for_onboarding_collaborator_missing_memory(monkeypatch):
    wm = _WM(flag=False)
    st, deps = _state(uuid.uuid4(), wm)
    _make(monkeypatch, state_obj=OnboardingState("onboarding", has_memory=False, has_connection=True, taps_emitted=0))
    await select_collaborator_onboarding_tap(st, deps)
    assert len(st.taps) == 1
    assert wm.signals.get("collaborator_onboarding_tap_emitted") is True


async def test_noop_when_already_active(monkeypatch):
    wm = _WM(flag=False)
    st, deps = _state(uuid.uuid4(), wm)
    _make(monkeypatch, state_obj=OnboardingState("active", has_memory=True, has_connection=True, taps_emitted=1))
    await select_collaborator_onboarding_tap(st, deps)
    assert st.taps == []


async def test_noop_when_already_emitted_this_session(monkeypatch):
    wm = _WM(flag=True)  # already emitted
    st, deps = _state(uuid.uuid4(), wm)
    _make(monkeypatch, state_obj=OnboardingState("onboarding", has_memory=False, has_connection=True, taps_emitted=0))
    await select_collaborator_onboarding_tap(st, deps)
    assert st.taps == []


async def test_noop_for_creator_no_row(monkeypatch):
    wm = _WM(flag=False)
    st, deps = _state(None, wm)  # no user_id
    await select_collaborator_onboarding_tap(st, deps)
    assert st.taps == []
```

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (module/function undefined).

- [ ] **Step 3: Implement the step**

Create `src/flashback/orchestrator/steps/select_collaborator_onboarding_tap.py`:

```python
"""Collaborator onboarding nudge — an indirect 'defining memory' tap card.

Fires once per session (WM flag) for an active collaborator whose
onboarding phase is still 'onboarding' and whose memory item is
unsatisfied. Reuses the tap-card surface; the significance is mined by
normal extraction. No intent gate — it nudges every session until the
collaborator records their first memory (then phase flips to 'active').
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog

from flashback.collaborator_onboarding import (
    get_onboarding_state,
    get_voice_anchor,
    increment_taps_emitted,
)
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.orchestrator.tap_options import generate_onboarding_tap
from flashback.phase_gate.queries import READ_PERSON_NAME_AND_GENDER

log = structlog.get_logger("flashback.orchestrator")


async def _read_name(deps: OrchestratorDeps, person_id: UUID) -> tuple[str, str | None]:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(READ_PERSON_NAME_AND_GENDER, {"person_id": person_id})
            row = await cur.fetchone()
    if row is None:
        return "", None
    return str(row[0]), None if row[1] is None else str(row[1])


async def select_collaborator_onboarding_tap(
    state: TurnState, deps: OrchestratorDeps
) -> None:
    with timed_step(log, "select_collaborator_onboarding_tap"):
        if state.user_id is None or state.taps:
            return
        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        if wm_state.collaborator_onboarding_tap_emitted:
            return

        async with deps.db_pool.connection() as conn:
            st = await get_onboarding_state(
                conn, person_id=state.person_id, user_id=state.user_id
            )
        if st is None or st.phase != "onboarding" or st.has_memory:
            return

        name, _gender = await _read_name(deps, state.person_id)
        async with deps.db_pool.connection() as conn:
            relationship = await get_voice_anchor(
                conn, person_id=state.person_id, user_id=state.user_id
            )
        relationship = relationship or state.person_relationship
        text, options = await generate_onboarding_tap(
            settings=deps.settings, person_name=name, relationship=relationship
        )
        tap = Tap(
            question_id=uuid4(),
            text=text,
            dimension="onboarding",
            options=options,
        )
        state.taps = [tap]
        await deps.working_memory.record_tap_emitted(
            session_id=str(state.session_id),
            question_id=str(tap.question_id),
            question_text=text,
        )
        await deps.working_memory.update_signals(
            session_id=str(state.session_id),
            collaborator_onboarding_tap_emitted=True,
        )
        async with deps.db_pool.connection() as conn:
            await increment_taps_emitted(
                conn, person_id=state.person_id, user_id=state.user_id
            )
            await conn.commit()
        log.info("collaborator_onboarding_tap.selected", person_id=str(state.person_id))
```

Ensure `get_onboarding_state`, `get_voice_anchor`, `increment_taps_emitted` are exported from `src/flashback/collaborator_onboarding/__init__.py`.

- [ ] **Step 4: Coverage-tap early-return**

At the very top of `select_coverage_tap` (in `select_coverage_tap.py`), right after the `with timed_step(...)`:

```python
        if state.taps:
            log.info("coverage_tap.skipped", reason="tap_already_set")
            return
```

- [ ] **Step 5: Export + wire into the pipeline**

In `src/flashback/orchestrator/steps/__init__.py`, export `select_collaborator_onboarding_tap`.

In `src/flashback/orchestrator/orchestrator.py`, in **both** `handle_turn` and `handle_turn_stream`, insert a step immediately before the `select_coverage_tap` execute block:

```python
        await execute(
            policies=TURN_POLICIES,
            step_name="select_collaborator_onboarding_tap",
            fn=lambda: select_collaborator_onboarding_tap(state, self._deps),
            state=state,
        )
```

Add the import at the top of `orchestrator.py` (alongside the other step imports).

- [ ] **Step 6: Run the new test** — `.venv/Scripts/python.exe -m pytest tests/orchestrator/test_select_collaborator_onboarding_tap.py -q -p no:warnings` — Expected: PASS (4).
- [ ] **Step 7: Orchestrator regression** — `.venv/Scripts/python.exe -m pytest tests/orchestrator -q -p no:warnings` (no new failures vs baseline; the `select_coverage_tap` early-return must not break its existing tests).
- [ ] **Step 8: Verify working tree.**

---

## Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the collaborator onboarding phase**

Add a short subsection (near §6 cold-start machinery, or as a note under invariant #26/#27) describing: the 2-item onboarding mirror; the sticky `collaborator_onboarding.phase` flag (distinct from Node's `onboarding_complete`); connection satisfied by voice anchor (form-mirrored **or** agent-inferred via extraction `contributor_relationship`, non-clobber) or modal resolution; memory satisfied by the first collaborator moment (`first_moment_id`); the Onboarding Check (guarded flip, runs at extraction-tx tail + session start); and the `select_collaborator_onboarding_tap` nudge (indirect, once/session, no ceiling). Note the agent never asks the relationship directly.

- [ ] **Step 2: Verify working tree.**

---

## Final verification

- [ ] **Full no-DB suite at baseline** — `TEST_DATABASE_URL= .venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings` — diff FAILED list vs baseline; zero new.
- [ ] **Full DB-gated suite** — `.venv/Scripts/python.exe -m pytest -q --tb=short -p no:warnings` (migration 0032 applied) — new SP tests green; zero new failures.
- [ ] **Manual flow (optional, dev UI):** create a legacy, switch to a collaborator → first turn shows the indirect memory tap card; answer it → after extraction, `collaborator_onboarding.phase` flips to `active` and the nudge stops next session.
