# Step 11 — Extraction Worker

The Extraction Worker is the second long-running process in the agent
service (after the embedding worker from step 3). It is the **first
component that writes to the canonical graph in production**.

It drains the `extraction` SQS queue, calls a Sonnet-class LLM to
extract structured memory data from each closed segment, runs
refinement detection against existing moments, and writes everything to
Postgres in a single transaction. Coverage Tracker, Handover Check, and
the idempotency row all ride along inside the same transaction.
Embedding and artifact-generation queue pushes fan out *after* commit.

## Process model

Sibling to `flashback.workers.embedding`: sync `boto3`, sync `psycopg`,
no async on the loop. The two LLM calls (`call_with_tool`) are
async-only, so the worker runs them through `asyncio.run()` per call.
**One message at a time** — segments are fully isolated units; batching
the LLM call across them is a correctness hazard and a rollback hazard.

Run it:

```
python -m flashback.workers.extraction run
```

Configuration comes from environment variables (`ExtractionConfig`). See
`.env.example` for the full list.

## The two LLM calls

| Call | Model | Fires | Purpose |
|---|---|---|---|
| Extraction | `LLM_BIG_MODEL` (Sonnet) | once per message | Returns moments + entities + traits + edges + dropped-reference questions |
| Compatibility | `LLM_SMALL_MODEL` (gpt-5.1) | once per refinement candidate | Decides `refinement | contradiction | independent` |

The compatibility call only fires when the vector search finds a
candidate within the cosine-distance threshold. For most segments it
fires zero times.

## Persistence transaction boundaries

```
BEGIN
  -- subject guard drops self-referential entities (no DB I/O)
  INSERT entities ...
  INSERT traits ...
  INSERT moments ...
  -- supersession (per moment with supersedes_id):
    UPDATE moments SET status='superseded', superseded_by=...
    DELETE FROM edges WHERE inbound conflict on UNIQUE (...)
    UPDATE edges SET to_id=new WHERE to_kind='moment' AND to_id=old
    DELETE FROM edges WHERE from_kind='moment' AND from_id=old
  INSERT edges (involves, happened_at, exemplifies, related_to)
    -- every edge validated by validate_edge() first
  INSERT questions (dropped_reference)
  INSERT edges (answered_by) for seeded_question_id, if any
  -- Coverage Tracker
  UPDATE persons SET coverage_state = jsonb_build_object(...)
  -- Handover Check
  UPDATE persons SET phase='steady', phase_locked_at=now() WHERE all dims >= 1
  INSERT processed_extractions (sqs_message_id ...)
COMMIT
```

After commit (failure here doesn't roll back the graph):

* Push embedding jobs (one per moment, entity-with-description, trait,
  question).
* Push artifact jobs (video for each moment, image for each entity).
* Re-read `active_moments` and log `would_trigger_thread_detector` if
  the count delta has crossed 15. Step 14 will replace the log with a
  queue push.
* Ack the SQS message.

If anything before commit raises, the worker rolls back and **does not
ack** the SQS message. SQS visibility timeout will redeliver.

## Query embedding vs stored embedding (invariant #4 reading)

CLAUDE.md invariant #4 — *"never generate embeddings inline"* — governs
**stored** embeddings. Stored vectors must always flow through the
embedding queue so the model identity stamping stays in lockstep
(invariant #3).

The refinement search uses the new moment's narrative embedded **as a
query** (`input_type="query"`), in-process, by `SyncVoyageQueryEmbedder`.
The vector is consumed for the similarity search and discarded —
nothing is written to a vector column. This is the same pattern step 6
(Retrieval Service) established for the turn hot path.

## Coverage Tracker / Handover Check inline behaviour

Both run inside the persistence transaction. They're UPDATE statements
on `persons` and they need to see the just-written moments — so
logically they run "after" extraction but transactionally they're part
of the same unit.

* **Coverage Tracker** (`coverage.py`) increments per-dimension counters
  per moment based on simple booleans the persistence layer surfaces:
  - `sensory` — `sensory_details` non-empty
  - `voice` — a trait was extracted in this segment, OR a linked person
    entity has a `saying`/`mannerism` attribute
  - `place` — any `involves` (or `happened_at`) edge to a `place` entity
  - `relation` — any `involves` edge to a person entity (≠ subject; the
    subject guard already removed self-references)
  - `era` — `time_anchor.year` set, OR `life_period_estimate` set
* **Handover Check** (`handover.py`) flips `persons.phase` to `'steady'`
  iff all five dimensions are ≥ 1. Sticky. Admin can reset via the
  endpoint added in step 4.

## Thread Detector trigger (logging-only for now)

Per ARCHITECTURE.md §3.13:

```
total active moments ≥ 15 AND
(active_count - moments_at_last_thread_run) ≥ 15
```

Step 11 only logs the trigger condition (`would_trigger_thread_detector`
with the count delta). Step 14 will add the queue push.

## The 0.35 distance threshold

Tuning knob via `EXTRACTION_REFINEMENT_DISTANCE_THRESHOLD`. Lower =
stricter (fewer candidates surfaced; more "independent" memories preserved).
Higher = looser (more candidates surfaced; more compatibility-LLM calls).

It's a starting guess. Watch:

* False refinements (memories merged that shouldn't have been) — lower it.
* Missed merges (the same memory appears as two moments) — raise it.

## Files added

```
migrations/
├── 0003_extraction_worker_support.up.sql
└── 0003_extraction_worker_support.down.sql

src/flashback/workers/extraction/
├── __init__.py
├── __main__.py             — CLI entrypoint
├── compatibility_llm.py    — gpt-5.1 wrapper
├── coverage.py             — Coverage Tracker
├── extraction_llm.py       — Sonnet wrapper
├── handover.py             — Handover Check
├── idempotency.py          — processed_extractions read/write
├── persistence.py          — transactional writer + subject guard
├── post_commit.py          — embedding/artifact queue pushes
├── prompts.py              — system prompts + ToolSpecs
├── refinement.py           — vector + entity-overlap search
├── schema.py               — pydantic models
├── sqs_client.py           — sync SQS clients (in + 2 out)
├── thread_trigger.py       — Thread Detector trigger logging
├── voyage_query.py         — sync Voyage query embedder
└── worker.py               — drain loop + ExtractionWorker
```

`src/flashback/config.py` gained `ExtractionConfig`.

## Verified

- Migration 0003 applies cleanly via the existing `schema_applied`
  fixture (which picks up every `*.up.sql` automatically).
- `python -c "import flashback.workers.extraction"` loads the package.
- `python -m flashback.workers.extraction --help` shows the `run`
  subcommand.
- `pytest tests/workers/extraction/` runs 50 tests; all pass against
  the local Postgres+pgvector instance.
- The full suite (`pytest tests/`) is **290 passed**.

## Cost shape (per message)

Typical (no refinement candidates):

* 1 × Sonnet call (extraction). 4k max tokens.
* 0 × gpt-5.1 calls.

When the new moment looks like an existing one (vector hit + entity
overlap):

* 1 × Sonnet call.
* 1–3 × gpt-5.1 calls (one per surviving candidate; we stop on the
  first `refinement` verdict).

Postgres I/O is bounded: at most 3 moments, ~10 entities, a handful of
edges per segment.

## Notable deviations from the original prompt

1. **Sync worker, async LLM**. The prompt says "sync, no async". The
   LLM interface from earlier steps is async-only (`call_with_tool`),
   so the worker runs each LLM call through `asyncio.run()`. That keeps
   the loop sync (matching the embedding worker's process model) while
   reusing the existing async LLM adapter without forking it.

2. **`ARTIFACT_QUEUE_URL` is required, not optional**. The CLI
   constructs an `ArtifactJobSender` at startup; making the URL optional
   would mean conditionalising every push. The `.env.example` includes
   a stub URL.

3. **Subject guard preserves the entity index map**. The prompt sketch
   showed dropping self-referential entities mid-flight, but moments
   reference entities by index and a dropped entity would shift those
   indexes. We keep the original ordering and map dropped indexes to
   `None`; persistence skips edges whose target index resolves to
   `None`. This means a moment that referenced [subject_self, kitchen]
   ends up with one `involves` edge (to kitchen), not two — exactly
   the behaviour exercised in `test_persistence_subject_guard.py`.

4. **`happened_at` to a non-place entity is dropped, not raised**. The
   schema validator (`validate_edge`) only checks `(from_kind, to_kind,
   edge_type)` shape; sub-kind enforcement (`happened_at` requires
   `kind='place'`) lives in app code. We log and skip the edge instead
   of aborting the transaction, since the LLM occasionally mis-tags an
   entity and dropping the edge degrades gracefully.

5. **Refinement returns up to N candidates (default 3) and we let the
   compatibility LLM rank them**. The prompt sketched a "first
   refinement match wins" loop, which we kept; we just made the
   per-message candidate cap configurable rather than hard-coded.

No drift from the migration's `CHECK` constraints — the
`test_prompts.py` drift detector parses `0001_initial_schema.up.sql`
and asserts the entity `kind` enum in
`flashback.workers.extraction.prompts.ENTITY_KINDS` matches one-for-one.
