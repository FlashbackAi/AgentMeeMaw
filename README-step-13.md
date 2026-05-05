# Step 13 — Trait Synthesizer

Step 13 stands up the **Trait Synthesizer**: a small-LLM background
worker that walks a single legacy's existing traits and active threads
and decides, in one LLM call, which traits to upgrade in strength,
which to downgrade (rare), and which new traits the thread evidence
supports. Per ARCHITECTURE.md §3.14, this runs as part of the
post-session sequence (Session Wrap → Extraction Worker drains →
**Trait Synthesizer** → Profile Summary → P2/P3/P5).

This is the smallest of the background workers in the build — short
prompt, single LLM call per person, modest schema impact. The
schema-touching change is one extension to `validate_edge()` so the
synthesizer can write `evidences` edges from threads to traits.

## What landed

### Migration

- `migrations/0005_trait_synthesizer_support.{up,down}.sql` — adds
  `processed_trait_syntheses` (idempotency), keyed by TEXT so the SQS
  path (MessageId) and the CLI path (`runonce-{person_id}-{ms}`) share
  a single table.

### Edge matrix extension

- `src/flashback/db/edges.py` — adds two tuples to the `evidences`
  edge type:
  - `("thread", "trait")` — written by the synthesizer.
  - `("entity", "trait")` — forward-compat shape, no v1 writer.
- `SCHEMA.md` §3.1 — row for `evidences` updated to match.
- `tests/db/test_edges_extension.py` — asserts the new tuples are
  accepted, old tuples still work, and reverse directions still raise.

### New package

```
src/flashback/workers/trait_synthesizer/
├── __init__.py
├── __main__.py            # CLI: run / run-once
├── worker.py              # Drain loop + per-message orchestration
├── runner.py              # run_once: idempotency + LLM + persistence + post-commit
├── synth_llm.py           # Single LLM call wrapper (asyncio.run shim)
├── prompts.py             # System prompt + tool definition
├── persistence.py         # Per-person transaction
├── context.py             # DB → LLM context assembly
├── idempotency.py         # processed_trait_syntheses helpers
├── schema.py              # Pydantic + dataclass models
└── sqs_client.py          # Producer + consumer for the trait_synthesizer queue
```

### Tests

`tests/workers/trait_synthesizer/` — 8 test files, **61 cases**:

- `test_prompts.py` — JSON Schema validity + drift detectors for
  `Strength` / `Action` enums and `minItems: 1` on
  `new_trait_proposals.supporting_thread_ids`.
- `test_schema.py` — pydantic round-trip, max-length name guard,
  Literal coverage, `extra="forbid"` on the result model.
- `test_synth_llm.py` — happy path; `LLMTimeout` and `LLMError`
  propagate; malformed UUID / missing required fields raise
  `ValidationError`.
- `test_context.py` — only active rows pulled, archived rows
  excluded; cross-legacy isolation; per-thread and per-trait moment
  counts (incl. that archived edges are ignored).
- `test_persistence.py` — upgrade/downgrade ladder steps;
  ladder-extreme no-ops; keep is a no-op; unknown trait id and
  cross-legacy ids skipped; new trait inserted with NULL embedding
  columns; case-insensitive duplicate-name guard; evidence edges
  written for both upgrades and new traits; `validate_edge` checks;
  transaction atomicity.
- `test_idempotency.py` — `make_runonce_key` shape + uniqueness;
  `is_processed` true after `mark_processed`; ON CONFLICT DO NOTHING
  preserves original row; works through cursor or connection.
- `test_runner.py` — happy path persists + pushes embedding once
  per new trait; same idempotency key short-circuits the second run;
  different keys for the same person each run independently.
- `test_worker.py` — happy path acks; `LLMTimeout` does NOT ack;
  `LLMMalformedResponse` and base `LLMError` ack (fail-soft);
  generic exceptions do NOT ack; redelivery of the same MessageId
  skips and acks both.

Plus `tests/db/test_edges_extension.py` — 9 cases.

### Configuration

`src/flashback/config.py` adds:

- `TraitSynthesizerConfig` — full env block, including
  `TRAIT_SYNTHESIZER_QUEUE_URL` and the
  `LLM_TRAIT_SYNTH_*` family (defaults to `LLM_SMALL_*`).
  `from_env(queue_required=False)` lets the `run-once` CLI path skip
  the queue URL for ad-hoc testing.

`.env.example` — new section listing the env vars added by this step.

`pyproject.toml` — no new top-level dependencies. The synthesizer
uses what's already in the tree: pydantic, structlog, psycopg, boto3,
plus the existing async LLM interface.

## How it works

### Strength ladder

Every trait carries a `strength` on a four-rung ladder:

```
mentioned_once → moderate → strong → defining
```

Upgrades and downgrades both move **one rung at a time**. The
synthesizer cannot jump rungs. A trait already at the top
(`defining`) cannot be upgraded; one already at the bottom
(`mentioned_once`) cannot be downgraded. Both edge cases log and are
counted as `skipped_at_ladder_extreme` in the persist result — the
trait row is otherwise untouched.

### `keep`-by-default bias

The system prompt explicitly biases toward `keep` in the existing-
trait decision pass. The wording: *"DEFAULT — choose this when in
doubt"*. Upgrades require the model to articulate that *multiple
threads or strong threads* support the trait beyond what the current
strength reflects. Downgrades require an explicit justification that
the existing threads don't actually support the current strength.

The shorter-than-input-trait-list shape of
`existing_trait_decisions` is allowed: traits that the model omits
default to `keep`. This avoids a long row of bookkeeping `keep`
entries when the model has nothing useful to say about them.

### Single LLM call

One `gpt-5.1` call per person, structured as one tool invocation
(`synthesize_traits`) that emits **both** decision pillars:

1. `existing_trait_decisions` — per-trait keep/upgrade/downgrade with
   reasoning and supporting thread ids (used for evidence edges).
2. `new_trait_proposals` — name + description + initial strength +
   ≥ 1 supporting thread + reasoning.

Cost shape per person: **1 small-LLM call**. That's why the worker
ships sync; there's no batching to hide.

### CLI `run-once` for ops/testing

```bash
python -m flashback.workers.trait_synthesizer run            # drain SQS
python -m flashback.workers.trait_synthesizer run-once \
    --person-id <uuid>                                       # one-shot
```

`run-once` shares the entire `runner.run_once` codepath with the
queue worker. Its only differences are (a) it builds an idempotency
key locally via `make_runonce_key()` (`runonce-{person_id}-{ms}`)
and (b) it doesn't talk to SQS. Idempotency is best-effort on this
path — same person twice in rapid succession produces two distinct
keys, hence two rows in `processed_trait_syntheses` and two LLM
calls. That's by design: the path is for ad-hoc testing, not steady
state.

### Fail-soft policy on permanent vs. transient LLM errors

The exception hierarchy is `LLMError` ← `LLMTimeout`,
`LLMMalformedResponse`. The worker catches the more specific
`LLMTimeout` first (do NOT ack — let SQS redrive on the visibility
timeout), then `LLMError` afterwards (ack — covers both
`LLMMalformedResponse` and any other permanent provider error). A
generic `Exception` does NOT ack (programmer error or DB outage —
SQS will redrive).

The fail-soft on permanent LLM errors is deliberate: trait
synthesis is *enhancement*, not critical. We'd rather drop one
synth than block the queue head on a malformed response. The next
session's wrap will trigger a new attempt anyway.

### Per-person transaction

Every decision for one person is a single Postgres transaction:

1. Apply existing-trait `upgrade`/`downgrade` UPDATEs and write
   `thread → trait` `evidences` edges from each supporting thread.
2. Insert new trait rows (skipping any whose name duplicates an
   active trait by case-insensitive match).
3. Write `thread → trait` `evidences` edges for each new trait's
   supporting threads.
4. Insert the idempotency row.

If anything raises mid-way, the surrounding transaction rolls back
and the SQS message is not acked. SQS visibility timeout will
redrive. The idempotency row is written *inside* the transaction,
so persisted-status and graph state move together.

### Embedding handling

New traits are pushed to the embedding queue **after commit**. The
source text follows the convention used elsewhere: `"{name}, {desc}"`
when description is set, otherwise `"{name}"`.

Existing traits whose strength changed are **not** re-embedded — the
embedded text (name + description) hasn't moved.

### Subject identity

Traits describe the SUBJECT of the legacy by construction (the
`traits` table has no "subject vs. not" distinction). The system
prompt makes this explicit; no extra code-level guard is needed.

### No retroactive moment → trait `exemplifies` linking

When a new trait is created, the synthesizer writes
`thread → trait` `evidences` edges from the cited threads. It does
**not** walk through those threads' moments to create new
`moment → trait` `exemplifies` edges. Reasoning: the Extraction
Worker is the canonical writer of `exemplifies` edges at extraction
time. The synthesizer operates strictly at the thread level.

## Verified

- ✅ `python -m pytest tests/workers/trait_synthesizer tests/db` —
  **70 passed** against a live test database (TEST_DATABASE_URL set).
- ✅ Migration `0005` applies and rolls back cleanly via the test
  fixture's `schema_applied` setup (it picks up every `*.up.sql`).
- ✅ `validate_edge('thread', 'trait', 'evidences')` and
  `validate_edge('entity', 'trait', 'evidences')` accepted; reverse
  directions still raise; old tuples unchanged.
- ✅ `python -m flashback.workers.trait_synthesizer --help` prints
  the two-subcommand parser; `run` and `run-once` are wired through
  `TraitSynthesizerConfig.from_env` and open the pool / SQS clients
  exactly like the sibling workers.
- ✅ End-to-end happy path in `test_worker.py`:
  - Seeded a person + thread; pushed one message.
  - LLM stub returned a single new-trait proposal.
  - Asserted: trait row inserted, embedding push fired once, SQS
    message acked.
- ✅ Idempotency + redrive paths in `test_runner.py` /
  `test_worker.py`:
  - Same key twice → second is a skip + ack.
  - Different keys for the same person → two independent runs.
  - `LLMTimeout` → no ack; `LLMMalformedResponse` and base
    `LLMError` → ack.
  - Generic `Exception` → no ack.

## Out of scope (deferred)

- **Profile Summary Generator.** Step 14.
- **Producers P2 / P3 / P5.** Step 15.
- **Session Wrap.** Step 16. The `trait_synthesizer` queue producer
  doesn't exist yet; the worker consumes from a queue that's fed by
  `run-once` for testing only. The `TraitSynthesizerJobSender` class
  is shipped here so step 16 has a producer to import.
- **`exemplifies` edge backfill.** The synthesizer doesn't write
  moment → trait edges. Extraction Worker remains the canonical
  writer for those.
- **Trait merge / split / archive by users.** v2.
- **Re-evaluation on thread DELETION.** If a thread that evidenced
  a trait is later archived (which v1 doesn't do automatically), the
  trait's `evidences` edges are now stale. Future cleanup task.
- **Per-trait LLM call.** A single batched call per person was a
  deliberate choice to keep the cost shape small; per-trait calls
  are a v2 conversation if the model under-performs at scale.

## Deviations from the prompt and edge cases worth flagging

1. **Repo-relative paths instead of placeholder paths.** The prompt
   referenced `src/db/edges.py`, `src/workers/trait_synthesizer/...`.
   The actual layout (per the rest of the repo) puts these under
   `src/flashback/db/edges.py` and
   `src/flashback/workers/trait_synthesizer/...`. Using the real
   paths.

2. **psycopg pool API.** The prompt's pseudocode used
   `with db_pool.transaction() as tx:`. The real pattern in this
   repo (and what psycopg v3 + `psycopg_pool.ConnectionPool`
   actually expose) is the three-step
   `with db_pool.connection() as conn: with conn.transaction(): with conn.cursor() as cur:`.
   Followed throughout, mirroring the extraction and thread_detector
   workers.

3. **SQS client shape.** The prompt sketched
   `sqs.receive_message(...)` directly against boto3. Wrapped in a
   typed client (`TraitSynthesizerSQSClient`) mirroring
   `ThreadDetectorSQSClient`, returning a list of
   `ReceivedTraitSynthMessage` and exposing `delete(receipt_handle)`.

4. **Exception ordering.** `LLMTimeout` and `LLMMalformedResponse`
   are both subclasses of `LLMError`. Catching `LLMTimeout` first
   (no ack) and `LLMError` afterwards (ack) is the only ordering
   that produces the prompt's intended semantics. Documented in
   `worker.py`.

5. **Sync surface.** The prompt's
   `async def build_context(...)` is inconsistent with the rest of
   the worker (sync, like its siblings). Implemented sync; the LLM
   call is the only async surface and uses `asyncio.run(...)` to
   bridge it, mirroring `naming_llm.py` and `extraction_llm.py`.

6. **Embedding sender threading.** The prompt's
   `push_new_trait_embeddings(sqs, ...)` referenced a free `sqs`
   global. Threaded an `EmbeddingJobSender` through the worker /
   runner / persistence call sites instead, like
   `thread_detector.persistence.process_cluster` does.

7. **Schema field stamping for new traits.** New trait rows are
   inserted with NULL `description_embedding`,
   `embedding_model`, and `embedding_model_version`. The embedding
   worker fills them in when it drains the embedding job. This
   matches the existing `_insert_traits` shape in
   `extraction.persistence` and respects invariant #4 (no inline
   embeddings).

8. **Cross-legacy + unknown-id guards on existing-trait decisions.**
   `_fetch_strength` filters on both `id` and `person_id` (against
   the active view), so a hallucinated id from another legacy or
   from an archived trait silently skips. Logged as
   `unknown_trait_id`.

9. **Case-insensitive duplicate-name guard.** Implemented as
   `lower(name) = lower(%s)` against `active_traits` for the
   person. Surfaced in the persist result as
   `skipped_duplicate_names` for observability.

10. **`active_*` view + GROUP BY interaction.** `active_traits` is a
    Postgres view, and Postgres does not propagate primary-key
    functional dependency through views. The `ORDER BY t.created_at`
    on the trait fetch therefore requires `t.created_at` in the
    `GROUP BY` list. Added; tests guard against regressions on the
    empty-context shape.
