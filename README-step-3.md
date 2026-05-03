# Step 3 - Embedding Worker

The embedding worker is the **only writer** of vector columns in the
canonical graph. It drains the `embedding` SQS queue, calls Voyage AI
in batches, and writes vectors back to Postgres in lockstep with
`embedding_model` + `embedding_model_version`.

This step also reorganises the repo into a proper Python package:

```
src/flashback/
    config.py
    db/
        __init__.py            (was: ./__init__.py from step 1)
        edges.py               (was: ./edges.py from step 1)
        connection.py          (NEW)
        embedding_targets.py   (NEW)
    workers/
        embedding/
            __main__.py        (NEW - CLI entrypoint)
            worker.py          (NEW - drain loop)
            backfill.py        (NEW - scan + enqueue)
            voyage_client.py   (NEW)
            sqs_client.py      (NEW)

migrations/
    0001_initial_schema.up.sql / .down.sql
    0002_seed_starter_questions.up.sql / .down.sql

tests/
    conftest.py
    workers/embedding/
        test_embedding_targets.py
        test_voyage_client.py
        test_worker.py
        test_backfill.py
```

## What it does

- **Long-poll SQS** for up to 10 messages per call (`WaitTimeSeconds=20`).
- **Group by `(embedding_model, embedding_model_version)`**, then make
  **one** Voyage batch call per group.
- **Version-guarded UPDATE** writes the vector + model + version
  together, but only when the row is `status='active'` and either has
  no embedding yet (`embedding_model IS NULL`) or already matches the
  model identity in the message. This is what enforces invariant #3
  (never mix vectors across models).
- **Ack rules:** UPDATE that returns 1 row -> ack. UPDATE that returns
  0 rows (row gone, status not active, model already moved on) -> **ack
  anyway** - retrying would be incorrect. Voyage failure -> do not ack
  the batch. DB exception -> do not ack the message. SQS visibility
  timeout handles redelivery.

## Configuration

Everything is environment-driven. See [`.env.example`](.env.example)
for the full list. The four "what model are we on" knobs are:

```
EMBEDDING_MODEL=voyage-3-large
EMBEDDING_MODEL_VERSION=2025-01-07
EMBEDDING_QUEUE_URL=...
VOYAGE_API_KEY=...
```

To roll forward to a new model: change `EMBEDDING_MODEL` /
`EMBEDDING_MODEL_VERSION`, then run the backfill CLI to schedule
re-embeds. Existing rows stay readable; the worker only updates them
once the new vector arrives.

## Running

### Worker (long-running)

```bash
python -m flashback.workers.embedding run
```

Sits idle on long-poll when the queue is empty (cheap). Stops on
SIGINT / SIGTERM. SQS visibility timeout handles in-flight redelivery
on shutdown.

### Backfill (one-shot)

```bash
# Dry-run: scan + report, do not enqueue
python -m flashback.workers.embedding backfill --dry-run

# Real: enqueue every active row with a NULL vector
python -m flashback.workers.embedding backfill

# Scoped: only one record type
python -m flashback.workers.embedding backfill --record-type question
```

Right after migrations 0001 + 0002, the only embeddable rows are the
15 starter-anchor questions seeded in 0002. The dry-run output
should report exactly that:

```
backfill summary (model=voyage-3-large version=2025-01-07, dry_run=True):
  moment     found=0     enqueued=0
  entity     found=0     enqueued=0
  thread     found=0     enqueued=0
  trait      found=0     enqueued=0
  question   found=15    enqueued=0
```

Backfill is the only producer in this step. Other producers (the
Extraction Worker, Thread Detector, Trait Synthesizer, etc.) ship in
later steps and push to the same queue.

## What the version guard protects against

Without the guard, this race is possible:

1. T0: writer enqueues a re-embed job for moment X with model `A`.
2. T1: operator rolls forward; new writes are now stamped with model `B`.
   A second re-embed job for X is enqueued with model `B`.
3. T2: the old (slow) Voyage call from step 1 finally returns.
4. T3: a naive UPDATE would now stomp X's `B`-vector with an `A`-vector,
   leaving the row claiming model `B` (from the second update if it
   already ran) but holding an `A`-vector - or vice versa.

The version-guarded UPDATE refuses to write unless `embedding_model`
is NULL or matches the message's identity exactly. The losing job's
UPDATE returns 0 rows and is acked as no-op. The completeness CHECK
in `SCHEMA.md` s6 ensures the three columns can never disagree.

## Tests

```bash
pip install -e ".[dev]"
TEST_DATABASE_URL=postgresql://... pytest
```

Tests are split into two tiers:

- **No-DB tier** (always runs): `test_embedding_targets.py`,
  `test_voyage_client.py`, plus the failure-path cases in
  `test_worker.py` / `test_backfill.py`. These mock Voyage and the
  pool with `unittest.mock`.
- **DB tier** (requires `TEST_DATABASE_URL` pointing at a Postgres
  instance with `vector` + `pgcrypto` extensions): the happy-path,
  guard-skip, status-skip, re-embed, and backfill tests. Schema is
  applied once per session.

`test_embedding_targets.py` is the structural canary: it parses
`migrations/0001_initial_schema.up.sql` and asserts that every table
and vector column the registry references actually exists. If a
future migration renames a column, this test fails before the worker
silently does.

## Verified

(Report what was actually exercised on this machine.)

- [ ] `pip install -e ".[dev]"` installs cleanly
- [ ] No-DB tests pass (`pytest tests/workers/embedding/test_embedding_targets.py
  tests/workers/embedding/test_voyage_client.py`)
- [ ] DB-tier tests pass against `TEST_DATABASE_URL`
  (Postgres 16 + pgvector 0.6+ recommended)
- [ ] `python -m flashback.workers.embedding backfill --dry-run`
  reports `question found=15` against a freshly migrated DB
- [ ] `python -m flashback.workers.embedding run` starts and idles on
  an empty queue (`Ctrl-C` to stop)

Fill these checkboxes after running locally with a real Postgres +
pgvector + LocalStack (or real SQS) configured.

## Out of scope (deferred)

- Producer side of the queue. Other writers (Extraction Worker,
  Thread Detector, etc.) push to `embedding` from their own steps.
- Metrics. Step 3 ships structured logs only; a metrics layer arrives
  with the broader observability work.
- Graceful shutdown beyond SIGINT/SIGTERM. SQS visibility timeout is
  the redelivery mechanism for in-flight work.
- Connection-pool tuning. Defaults `min_size=1, max_size=4` are right
  for a single-process worker.

## Next: step 4 - Conversation Gateway + Working Memory

Step 4 introduces the Valkey schema, hydration, and the agent's HTTP
surface for `POST /session/start` + `POST /turn`.
