# Extraction-Completion Signal (LISTEN/NOTIFY) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace UI polling for newly-extracted moments with a Postgres `LISTEN/NOTIFY` completion signal — emitted per segment inside the extraction transaction, with a durable status row as the backstop — so the frontend learns when a session's moments are ready (or that a segment finished and produced nothing).

**Architecture:** The Extraction Worker already writes an idempotency row (`processed_extractions`) and runs all persistence inside a single transaction ([worker.py:334-388](../../../src/flashback/workers/extraction/worker.py#L334-L388)). We extend that row with counts + an `is_final` flag + `status`, and issue `SELECT pg_notify('extraction_complete', <json>)` in the same transaction — so the notification delivers if and only if the transaction commits, never on rollback. Postgres is authoritative (the new `session_extraction_status` view); NOTIFY is the low-latency wake-up. Node holds a dedicated `LISTEN` connection and, on wake-up or reconnect, re-queries the view. The agent never calls Node. No new queue, no SNS, no outbox entry — NOTIFY participates in the existing transaction directly.

**Tech Stack:** Python, psycopg3, Postgres (LISTEN/NOTIFY), pytest. SQL migrations under `migrations/NNNN_*.up.sql` / `.down.sql`.

---

## Boundary & invariant notes (read before starting)

- The agent **never calls Node** ([CLAUDE.md](../../../CLAUDE.md) §3). NOTIFY is delivered by Postgres, not by us calling Node — this preserves the boundary.
- **Postgres is authoritative; the message is a trigger only** — same rule the artifact pipeline follows ([CLAUDE.md](../../../CLAUDE.md) §3). The NOTIFY payload carries identifiers + convenience counts; Node re-reads `session_extraction_status` for the authoritative set.
- A **zero-moment segment still notifies.** That is the entire point — it resolves the "is it still coming or will it never come?" ambiguity that breaks polling.
- This adds **no new queue and no new top-level service**, so it does not trip [CLAUDE.md](../../../CLAUDE.md) §11.

## File structure

- Create: `migrations/0025_extraction_completion_signal.up.sql` — add `entities_written`, `traits_written`, `is_final`, `status` to `processed_extractions`; create `session_extraction_status` view.
- Create: `migrations/0025_extraction_completion_signal.down.sql` — drop view + columns.
- Modify: `src/flashback/workers/extraction/idempotency.py` — extend `mark_processed` to write the new columns and emit the NOTIFY (gated on the insert actually happening).
- Modify: `src/flashback/workers/extraction/worker.py:362-368` — pass counts + `is_final` into `mark_processed`.
- Modify: `src/flashback/workers/extraction/schema.py:246-263` — add `is_final` to `ExtractionMessage`.
- Modify: `src/flashback/queues/extraction.py:18-48` — add `is_final` param + payload field on `push`.
- Modify: `src/flashback/orchestrator/steps/wrap_session.py:128-136` — push with `is_final=True`.
- Modify: `src/flashback/orchestrator/steps/detect_segment.py:104-113` — push with `is_final=False`.
- Modify (tests): `tests/queues/test_extraction.py`, `tests/workers/extraction/test_idempotency.py`, `tests/workers/extraction/conftest.py`, `tests/workers/extraction/test_worker.py`.
- Modify (docs): `NODE_INTEGRATION.md` §8.3, `CLAUDE.md` (new invariant), `API.md`.

---

### Task 1: Migration — status columns + read view

**Files:**
- Create: `migrations/0025_extraction_completion_signal.up.sql`
- Create: `migrations/0025_extraction_completion_signal.down.sql`
- Test: `tests/workers/extraction/test_completion_signal_migration.py`

- [ ] **Step 1: Write the up migration**

`migrations/0025_extraction_completion_signal.up.sql`:

```sql
-- ============================================================================
-- 0025_extraction_completion_signal.up.sql
-- Extraction-completion signal: durable per-segment status + read view.
-- ----------------------------------------------------------------------------
-- The Extraction Worker emits a transactional pg_notify('extraction_complete')
-- when a segment finishes. Postgres is authoritative (this status row); the
-- notification is the low-latency wake-up only. A zero-moment segment still
-- writes a row and still notifies, which is what lets the UI distinguish
-- "extraction finished, nothing extracted" from "still running".
--
-- `is_final` marks the wrap-forced tail segment of a session (invariant #12).
-- `status` is 'done' on the happy path; reserved for future 'failed' states.
-- ============================================================================

BEGIN;

ALTER TABLE processed_extractions
    ADD COLUMN entities_written INT     NOT NULL DEFAULT 0,
    ADD COLUMN traits_written   INT     NOT NULL DEFAULT 0,
    ADD COLUMN is_final         BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN status           TEXT    NOT NULL DEFAULT 'done';

-- Node-facing read surface. One row per extracted segment; Node groups by
-- session_id and aggregates (sum moments, bool_or(is_final)). Exposing a view
-- rather than the raw idempotency table decouples Node from our internal
-- mechanism.
CREATE VIEW session_extraction_status AS
SELECT
    session_id,
    person_id,
    sqs_message_id AS segment_message_id,
    moments_written,
    entities_written,
    traits_written,
    is_final,
    status,
    processed_at
FROM processed_extractions;

COMMIT;
```

- [ ] **Step 2: Write the down migration**

`migrations/0025_extraction_completion_signal.down.sql`:

```sql
-- 0025_extraction_completion_signal.down.sql
BEGIN;

DROP VIEW IF EXISTS session_extraction_status;

ALTER TABLE processed_extractions
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS is_final,
    DROP COLUMN IF EXISTS traits_written,
    DROP COLUMN IF EXISTS entities_written;

COMMIT;
```

- [ ] **Step 3: Write the failing test**

`tests/workers/extraction/test_completion_signal_migration.py`:

```python
"""Migration 0025 shape (DB-touching). The db_pool fixture applies all
migrations, so we assert the resulting schema directly."""

from __future__ import annotations


def test_processed_extractions_has_signal_columns(db_pool):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_name = 'processed_extractions'
                   AND column_name IN
                       ('entities_written','traits_written','is_final','status')
                 ORDER BY column_name
                """
            )
            rows = dict(cur.fetchall())
    assert rows == {
        "entities_written": "integer",
        "is_final": "boolean",
        "status": "text",
        "traits_written": "integer",
    }


def test_session_extraction_status_view_exists(db_pool):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_name = 'session_extraction_status'
                 ORDER BY column_name
                """
            )
            cols = {r[0] for r in cur.fetchall()}
    assert {
        "session_id", "person_id", "segment_message_id",
        "moments_written", "entities_written", "traits_written",
        "is_final", "status", "processed_at",
    } <= cols
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/workers/extraction/test_completion_signal_migration.py -v`
Expected: FAIL — columns/view don't exist yet (the test DB must be re-created so migration 0025 applies). If the project caches a migrated test DB, drop/recreate it per the repo's test-DB setup, then the test passes once 0025 is present.

- [ ] **Step 5: Apply migrations and re-run**

Run the repo's migration runner against the test DB (same mechanism the `db_pool` fixture relies on), then:
Run: `pytest tests/workers/extraction/test_completion_signal_migration.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add migrations/0025_extraction_completion_signal.up.sql \
        migrations/0025_extraction_completion_signal.down.sql \
        tests/workers/extraction/test_completion_signal_migration.py
git commit -m "feat(extraction): migration for completion-signal status columns + view"
```

---

### Task 2: `is_final` on the queue payload + message schema

**Files:**
- Modify: `src/flashback/queues/extraction.py:18-48`
- Modify: `src/flashback/workers/extraction/schema.py:254-263`
- Test: `tests/queues/test_extraction.py`

- [ ] **Step 1: Update the full-payload equality test (it will break) and add an `is_final` test**

In `tests/queues/test_extraction.py`, update `test_extraction_push_uses_architecture_payload_shape` so the expected dict includes the new field, and add a focused test:

```python
async def test_extraction_push_uses_architecture_payload_shape():
    sqs = CapturingSQS()
    producer = ExtractionQueueProducer(sqs, "queue-url")
    session_id = uuid4()
    person_id = uuid4()
    question_id = UUID("55555555-5555-5555-5555-555555555555")

    message_id = await producer.push(
        session_id=session_id,
        person_id=person_id,
        segment_turns=SAMPLE_SEGMENT,
        rolling_summary="New summary.",
        prior_rolling_summary="Old summary.",
        seeded_question_id=question_id,
    )

    assert message_id == "msg-456"
    assert sqs.queue_url == "queue-url"
    assert sqs.body == {
        "session_id": str(session_id),
        "person_id": str(person_id),
        "segment_turns": [
            turn.model_dump(mode="json") for turn in SAMPLE_SEGMENT
        ],
        "rolling_summary": "New summary.",
        "prior_rolling_summary": "Old summary.",
        "seeded_question_id": str(question_id),
        "candidate_question_ids": [],
        "contributor_display_name": "",
        "is_final": False,
    }


async def test_extraction_push_marks_final_segment():
    sqs = CapturingSQS()
    producer = ExtractionQueueProducer(sqs, "queue-url")

    await producer.push(
        session_id=uuid4(),
        person_id=uuid4(),
        segment_turns=SAMPLE_SEGMENT,
        rolling_summary="",
        prior_rolling_summary="",
        seeded_question_id=None,
        is_final=True,
    )

    assert sqs.body["is_final"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/queues/test_extraction.py -v`
Expected: FAIL — `push()` has no `is_final` kwarg / payload lacks the key.

- [ ] **Step 3: Add `is_final` to the producer**

In `src/flashback/queues/extraction.py`, add the parameter (defaulting `False`) and the payload field:

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
        is_final: bool = False,
    ) -> str:
        """Push an extraction job and return the SQS MessageId."""

        payload = {
            "session_id": str(session_id),
            "person_id": str(person_id),
            "segment_turns": [
                turn.model_dump(mode="json") for turn in segment_turns
            ],
            "rolling_summary": rolling_summary,
            "prior_rolling_summary": prior_rolling_summary,
            "seeded_question_id": (
                str(seeded_question_id) if seeded_question_id else None
            ),
            "candidate_question_ids": [
                str(question_id) for question_id in (candidate_question_ids or [])
            ],
            "contributor_display_name": contributor_display_name or "",
            "is_final": is_final,
        }
        return await self._sqs.send_message(self._url, payload)
```

- [ ] **Step 4: Add `is_final` to `ExtractionMessage`**

In `src/flashback/workers/extraction/schema.py`, add the field after `contributor_display_name` (default `False` keeps old in-flight messages valid):

```python
    contributor_display_name: str = ""
    is_final: bool = False
    """True only for the wrap-forced tail segment of a session (invariant #12).
    Drives the completion signal's session-complete flag."""
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/queues/test_extraction.py tests/workers/extraction/test_prompts.py -v`
Expected: PASS (the prompts drift test should still pass — `ExtractionMessage` is the queue payload model, not the tool-output model).

- [ ] **Step 6: Commit**

```bash
git add src/flashback/queues/extraction.py \
        src/flashback/workers/extraction/schema.py \
        tests/queues/test_extraction.py
git commit -m "feat(extraction): carry is_final through the extraction queue payload"
```

---

### Task 3: Set `is_final` at the two push sites

**Files:**
- Modify: `src/flashback/orchestrator/steps/wrap_session.py:128-136`
- Modify: `src/flashback/orchestrator/steps/detect_segment.py:104-113`
- Test: `tests/orchestrator/test_segment_turn_sequence.py` (assert per-site `is_final`)

- [ ] **Step 1: Write/extend a failing test asserting both sites**

Add to `tests/orchestrator/test_segment_turn_sequence.py` (adapt the existing capturing-queue stub used in that module; it records `push` kwargs). If the module's stub does not capture kwargs, add a `is_final` capture to it:

```python
async def test_wrap_push_marks_is_final_true(wrap_deps_and_state):
    # Arrange: a session with an open segment that wrap will flush.
    deps, state = wrap_deps_and_state
    await wrap_session(state, deps)
    assert deps.extraction_queue.last_push_kwargs["is_final"] is True


async def test_inline_segment_push_marks_is_final_false(turn_deps_and_state_at_boundary):
    deps, state = turn_deps_and_state_at_boundary
    await detect_segment(state, deps)
    assert deps.extraction_queue.last_push_kwargs["is_final"] is False
```

> Note: reuse the existing fixtures in this test module that build `deps`/`state` and force a boundary. If the queue stub there only records positionally, add `self.last_push_kwargs = kwargs` to its `push`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/orchestrator/test_segment_turn_sequence.py -v`
Expected: FAIL — pushes don't pass `is_final` yet (KeyError on `last_push_kwargs["is_final"]`).

- [ ] **Step 3: Pass `is_final=True` from wrap**

In `src/flashback/orchestrator/steps/wrap_session.py`, add the kwarg to the push:

```python
        await deps.extraction_queue.push(
            session_id=state.session_id,
            person_id=state.person_id,
            segment_turns=segment_turns,
            rolling_summary=result.rolling_summary or "",
            prior_rolling_summary=prior_rolling_summary,
            seeded_question_id=seeded_question_id,
            contributor_display_name=wm_state.contributor_display_name or "",
            is_final=True,
        )
```

- [ ] **Step 4: Pass `is_final=False` from the inline path**

In `src/flashback/orchestrator/steps/detect_segment.py`, add the explicit kwarg to the push:

```python
        message_id = await deps.extraction_queue.push(
            session_id=state.session_id,
            person_id=state.person_id,
            segment_turns=segment_turns,
            rolling_summary=result.rolling_summary or "",
            prior_rolling_summary=prior_rolling_summary,
            seeded_question_id=seeded_question_id,
            candidate_question_ids=candidate_question_ids,
            contributor_display_name=wm_state.contributor_display_name or "",
            is_final=False,
        )
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/orchestrator/test_segment_turn_sequence.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/flashback/orchestrator/steps/wrap_session.py \
        src/flashback/orchestrator/steps/detect_segment.py \
        tests/orchestrator/test_segment_turn_sequence.py
git commit -m "feat(extraction): mark wrap segment is_final, inline segments not"
```

---

### Task 4: `mark_processed` writes status columns + emits the NOTIFY

**Files:**
- Modify: `src/flashback/workers/extraction/idempotency.py`
- Test: `tests/workers/extraction/test_idempotency.py`

- [ ] **Step 1: Write failing tests (columns, notify, no-double-notify)**

Append to `tests/workers/extraction/test_idempotency.py`:

```python
import json


def test_mark_processed_writes_signal_columns(db_pool, make_person):
    person_id = make_person("Idem Cols")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            mark_processed(
                cur,
                message_id=message_id,
                person_id=person_id,
                session_id=session_id,
                moments_written=3,
                entities_written=2,
                traits_written=1,
                is_final=True,
            )
        conn.commit()

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT moments_written, entities_written, traits_written,
                       is_final, status
                  FROM processed_extractions WHERE sqs_message_id=%s
                """,
                (message_id,),
            )
            row = cur.fetchone()
    assert row == (3, 2, 1, True, "done")


def test_mark_processed_emits_notification(db_pool, make_person):
    person_id = make_person("Notify One")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as listen_conn:
        listen_conn.autocommit = True
        listen_conn.execute("LISTEN extraction_complete")
        try:
            with db_pool.connection() as conn:
                with conn.cursor() as cur:
                    mark_processed(
                        cur,
                        message_id=message_id,
                        person_id=person_id,
                        session_id=session_id,
                        moments_written=0,   # zero-moment segment STILL notifies
                        is_final=True,
                    )
                conn.commit()

            # psycopg3 generator; stop after the first notification or 5s.
            received = [n for n in listen_conn.notifies(timeout=5, stop_after=1)]
        finally:
            listen_conn.execute("UNLISTEN *")

    assert len(received) == 1
    payload = json.loads(received[0].payload)
    assert payload["event"] == "extraction_complete"
    assert payload["session_id"] == session_id
    assert payload["segment_message_id"] == message_id
    assert payload["is_final"] is True
    assert payload["moments_written"] == 0
    assert payload["status"] == "done"


def test_mark_processed_does_not_double_notify_on_conflict(db_pool, make_person):
    person_id = make_person("Notify Dedup")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as listen_conn:
        listen_conn.autocommit = True
        listen_conn.execute("LISTEN extraction_complete")
        try:
            with db_pool.connection() as conn:
                with conn.cursor() as cur:
                    first = mark_processed(
                        cur, message_id=message_id, person_id=person_id,
                        session_id=session_id, moments_written=1,
                    )
                    second = mark_processed(  # same id -> ON CONFLICT DO NOTHING
                        cur, message_id=message_id, person_id=person_id,
                        session_id=session_id, moments_written=1,
                    )
                conn.commit()

            received = [n for n in listen_conn.notifies(timeout=2, stop_after=2)]
        finally:
            listen_conn.execute("UNLISTEN *")

    assert first is True
    assert second is False
    assert len(received) == 1  # only the inserting call notified
```

> Note (psycopg version): `Connection.notifies(timeout=..., stop_after=...)` is psycopg ≥ 3.2. Confirm the pinned version in `pyproject.toml`; if older, drain with the `conn.notifies()` generator inside a `select`-based timeout per that version's API. The `db_pool` must allow ≥ 2 concurrent connections (the listener + the writer) — the default pool size in `conftest` satisfies this.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/workers/extraction/test_idempotency.py -v`
Expected: FAIL — `mark_processed` rejects the new kwargs / returns `None` / no notification fires.

- [ ] **Step 3: Rewrite `mark_processed`**

Replace `mark_processed` in `src/flashback/workers/extraction/idempotency.py` (add `import json` at top):

```python
from __future__ import annotations

import json
from typing import Any

NOTIFY_CHANNEL = "extraction_complete"


def is_processed(conn_or_cursor, message_id: str) -> bool:
    """Return True iff this MessageId already has a processed_extractions row."""
    cur = _as_cursor(conn_or_cursor)
    cur.execute(
        "SELECT 1 FROM processed_extractions WHERE sqs_message_id = %s",
        (message_id,),
    )
    return cur.fetchone() is not None


def mark_processed(
    cursor,
    *,
    message_id: str,
    person_id: str,
    session_id: str,
    moments_written: int,
    entities_written: int = 0,
    traits_written: int = 0,
    is_final: bool = False,
    status: str = "done",
) -> bool:
    """Record the segment as processed AND announce completion.

    Inserts the idempotency/status row and, only when that insert actually
    happens (i.e. not an ON CONFLICT no-op redelivery), issues a
    transactional ``pg_notify`` on ``extraction_complete``. Because the
    NOTIFY runs in the caller's transaction, it is delivered iff that
    transaction commits and never on rollback — so a failed extraction
    announces nothing, and a zero-moment success still announces (with
    ``moments_written=0``). Returns True iff a row was inserted.

    Postgres is authoritative (the ``session_extraction_status`` view);
    this notification is the low-latency wake-up only (CLAUDE.md §3).
    """
    cursor.execute(
        """
        INSERT INTO processed_extractions
              (sqs_message_id, person_id, session_id, moments_written,
               entities_written, traits_written, is_final, status)
        VALUES (%s,            %s,        %s,         %s,
               %s,              %s,             %s,       %s)
        ON CONFLICT (sqs_message_id) DO NOTHING
        RETURNING sqs_message_id
        """,
        (
            message_id, person_id, session_id, moments_written,
            entities_written, traits_written, is_final, status,
        ),
    )
    inserted = cursor.fetchone() is not None
    if inserted:
        payload = json.dumps(
            {
                "event": "extraction_complete",
                "session_id": session_id,
                "person_id": person_id,
                "segment_message_id": message_id,
                "is_final": is_final,
                "status": status,
                "moments_written": moments_written,
            }
        )
        cursor.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, payload))
    return inserted


def _as_cursor(conn_or_cursor: Any):
    """Accept either a psycopg connection or a cursor."""
    if hasattr(conn_or_cursor, "execute"):
        return conn_or_cursor
    return conn_or_cursor.cursor()
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/workers/extraction/test_idempotency.py -v`
Expected: PASS (all five tests — the two original + three new).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/workers/extraction/idempotency.py \
        tests/workers/extraction/test_idempotency.py
git commit -m "feat(extraction): mark_processed writes status + emits transactional NOTIFY"
```

---

### Task 5: Wire counts + `is_final` into the worker

**Files:**
- Modify: `src/flashback/workers/extraction/worker.py:362-368`
- Modify: `tests/workers/extraction/conftest.py:139-178` (add `is_final` to `make_received_message`)
- Test: `tests/workers/extraction/test_worker.py`

- [ ] **Step 1: Add `is_final` to the test message builder**

In `tests/workers/extraction/conftest.py`, add the param + payload key to `make_received_message`:

```python
def make_received_message(
    *,
    person_id: str,
    session_id: str | None = None,
    seeded_question_id: str | None = None,
    segment_turns: list[dict] | None = None,
    rolling_summary: str = "",
    prior_rolling_summary: str = "",
    receipt_handle: str | None = None,
    message_id: str | None = None,
    is_final: bool = False,
) -> ReceivedMessage:
```

and inside the validated payload dict add:

```python
            "seeded_question_id": seeded_question_id,
            "is_final": is_final,
        }
    )
```

- [ ] **Step 2: Write the failing worker test**

Append to `tests/workers/extraction/test_worker.py` (mirror an existing happy-path test's wiring — the module already mocks the extraction/compat LLMs and uses `_build_worker`; reuse that fixture pattern). The new test asserts the status row reflects counts + `is_final`:

```python
def test_worker_records_is_final_and_counts(
    db_pool, make_person, extraction_cfg, compat_cfg, trait_merge_cfg,
    settings, monkeypatch,
):
    # Reuse the module's happy-path LLM stubbing. SAMPLE returns >=1 moment.
    monkeypatch.setattr(
        ext_llm_mod, "call_with_tool",
        _stub_call(sample_extractions.SINGLE_MOMENT_TOOL_OUTPUT),
    )
    person_id = make_person("Final Flag")
    worker = _build_worker(
        db_pool=db_pool, extraction_cfg=extraction_cfg, compat_cfg=compat_cfg,
        trait_merge_cfg=trait_merge_cfg, settings=settings,
    )
    msg = make_received_message(person_id=person_id, is_final=True)

    worker.process_message(msg)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT moments_written, is_final, status
                  FROM processed_extractions WHERE sqs_message_id=%s
                """,
                (msg.message_id,),
            )
            row = cur.fetchone()
    assert row is not None
    moments_written, is_final, status = row
    assert is_final is True
    assert status == "done"
    assert moments_written >= 1
```

> Note: use whichever sample fixture in `tests/workers/extraction/fixtures/sample_extractions.py` yields ≥1 moment with no refinement candidates (the `StubVoyage(return_none=True)` default produces no candidates, so no compat call is needed). If the constant name differs, use the one the existing happy-path worker test imports.

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/workers/extraction/test_worker.py::test_worker_records_is_final_and_counts -v`
Expected: FAIL — `is_final` column is always default `False` because the worker doesn't pass it yet.

- [ ] **Step 4: Pass counts + `is_final` in the worker**

In `src/flashback/workers/extraction/worker.py`, update the `mark_processed` call:

```python
                    mark_processed(
                        cur,
                        message_id=message_id,
                        person_id=str(payload.person_id),
                        session_id=str(payload.session_id),
                        moments_written=len(persistence_result.moment_ids),
                        entities_written=len(persistence_result.entity_ids),
                        traits_written=len(persistence_result.trait_ids),
                        is_final=payload.is_final,
                        status="done",
                    )
```

- [ ] **Step 5: Run to verify pass (and no regressions in the worker suite)**

Run: `pytest tests/workers/extraction/test_worker.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/flashback/workers/extraction/worker.py \
        tests/workers/extraction/conftest.py \
        tests/workers/extraction/test_worker.py
git commit -m "feat(extraction): worker records counts + is_final on completion signal"
```

---

### Task 6: Documentation — flip the contract from "don't poll" to "listen"

**Files:**
- Modify: `NODE_INTEGRATION.md` §8.3
- Modify: `CLAUDE.md` (new invariant after #24)
- Modify: `API.md` (document the channel payload + view columns)

- [ ] **Step 1: Rewrite NODE_INTEGRATION.md §8.3**

Replace the body of §8.3 (currently "Don't poll the agent — just re-query Postgres on next page load") with:

```markdown
### 8.3 Extraction completes asynchronously — listen, don't poll

`/session/wrap` returns after pushing the tail segment onto the
`extraction` queue. Moments/entities/traits land in Postgres later, as the
Extraction Worker drains each segment.

**Do not poll for the appearance of moment rows.** A segment can legitimately
extract **zero** moments (under-extraction, invariant #6), so "no row yet" is
ambiguous between "still running" and "nothing will ever come" — no polling
interval resolves that.

Instead, the agent emits a Postgres `NOTIFY` on channel
**`extraction_complete`** inside the extraction transaction, once per segment.
Payload (JSON, identifiers + convenience counts; Postgres is authoritative):

​```json
{
  "event": "extraction_complete",
  "session_id": "…",
  "person_id": "…",
  "segment_message_id": "…",
  "is_final": true,
  "status": "done",
  "moments_written": 3
}
​```

**Node integration:**
- Hold one dedicated `LISTEN extraction_complete` connection (a direct/session-
  pinned connection — a transaction-mode pooler such as PgBouncer drops
  LISTEN). On each notification, push to the browser.
- Treat the NOTIFY as a wake-up only; read the authoritative set from the
  **`session_extraction_status`** view (`WHERE session_id = …`), aggregating
  `sum(moments_written)` and `bool_or(is_final)`.
- `is_final = true` marks the session's tail segment — the cue to render the
  final "session complete, N new moments" state (N may be 0).
- **Durability backstop:** on listener (re)connect, re-query
  `session_extraction_status` for any rows newer than your last-seen
  `processed_at` watermark to catch notifications missed while disconnected.
- A segment that permanently fails extraction (DLQ) emits no notification; if
  no `is_final` arrives within your wrap timeout, surface "still processing"
  and re-query.
```

- [ ] **Step 2: Add invariant #25 to CLAUDE.md**

After invariant #24, add:

```markdown
25. **Extraction completion is announced via transactional `NOTIFY`,
    not polling.** When the Extraction Worker commits a segment, it
    issues `pg_notify('extraction_complete', …)` inside the persistence
    transaction (in `mark_processed`). Because it is transactional, the
    notification fires iff the commit succeeds and never on rollback; a
    **zero-moment segment still notifies** (that is what disambiguates
    "finished empty" from "still running" for the UI). Postgres is
    authoritative — the durable per-segment status lives on
    `processed_extractions` and is exposed to Node via the
    `session_extraction_status` view; the notification carries only
    identifiers + convenience counts (mirrors the artifact trigger rule
    in §3). `is_final` marks the wrap-forced tail segment (#12). No new
    queue and no call to Node — delivery is via the shared Postgres the
    boundary already grants Node read access to.
```

- [ ] **Step 3: Document the surface in API.md**

Add a short subsection (near the async-timing / queue docs) describing the `extraction_complete` channel payload and the `session_extraction_status` view columns (`session_id, person_id, segment_message_id, moments_written, entities_written, traits_written, is_final, status, processed_at`), noting Node reads the view directly.

- [ ] **Step 4: Commit**

```bash
git add NODE_INTEGRATION.md CLAUDE.md API.md
git commit -m "docs(extraction): completion-signal contract (LISTEN/NOTIFY + status view)"
```

---

## Self-review

**Spec coverage:**
- PG-authoritative status row → Task 1 (columns) + Task 4/5 (writes). ✓
- SNS replaced by LISTEN/NOTIFY → Task 4 (transactional `pg_notify`). ✓
- Zero-moment segment still notifies → Task 4 `test_mark_processed_emits_notification` uses `moments_written=0`. ✓
- Notify gated on actual insert (no double-notify on redelivery) → Task 4 `RETURNING` + `test_..._does_not_double_notify_on_conflict`. ✓
- `is_final` from wrap, not inline → Tasks 2–3 + 5. ✓
- Node read surface decoupled from idempotency table → `session_extraction_status` view (Task 1). ✓
- Durability backstop + DLQ gap + pooler caveat documented → Task 6. ✓
- No new queue / no call to Node → invariant #25 wording + design. ✓

**Placeholder scan:** Two explicit "Note" callouts (psycopg `notifies` version; sample-fixture constant name) point at repo-specific facts the engineer must confirm — not skipped work. All code steps contain full code.

**Type consistency:** `mark_processed(... is_final, status, entities_written, traits_written) -> bool` is identical across idempotency.py (Task 4), the worker call (Task 5), and all tests. Payload keys (`event, session_id, person_id, segment_message_id, is_final, status, moments_written`) match between `mark_processed`, the worker test, and the docs. View column set matches between the migration (Task 1) and the docs (Task 6).
