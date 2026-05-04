# Step 14 — Profile Summary Generator

Step 14 stands up the **Profile Summary Generator**: a big-LLM
background worker that produces the prose `persons.profile_summary`
text shown at the top of a legacy page. Per ARCHITECTURE.md §3.15,
this is the third stop in the post-session sequence
(Session Wrap → Extraction Worker drains → Trait Synthesizer →
**Profile Summary** → P2/P3/P5).

This is the smallest of the background workers by component count.
The schema impact is one idempotency table; `persons.profile_summary`
itself already existed from migration 0001. The interesting work is
in the context-assembly code that picks the right slice of the
canonical graph for the LLM.

## What landed

### Migration

- `migrations/0006_profile_summary_support.{up,down}.sql` — adds
  `processed_profile_summaries` (idempotency), keyed by TEXT so the
  SQS path (MessageId) and the CLI path (`runonce-{person_id}-{ms}`)
  share one table.

### New package

```
src/flashback/workers/profile_summary/
├── __init__.py
├── __main__.py            # CLI: run / run-once
├── worker.py              # Drain loop + per-message orchestration
├── runner.py              # run_once: idempotency + LLM + persistence
├── summary_llm.py         # Single call_text() wrapper (asyncio.run shim)
├── prompts.py             # System prompt (prose, no tool)
├── persistence.py         # Per-person UPDATE + idempotency row
├── context.py             # DB → LLM context assembly + render_context
├── time_period.py         # Year range / life-period derivation (code, not LLM)
├── idempotency.py         # processed_profile_summaries helpers
├── schema.py              # Pydantic + dataclass models
└── sqs_client.py          # Producer + consumer for the profile_summary queue
```

### Tests

`tests/workers/profile_summary/` — 8 test files:

- `test_prompts.py` — drift detector for the negative-constraint
  clauses (no platitudes, no impersonation), name + length contract.
- `test_summary_llm.py` — happy path strips whitespace; empty /
  whitespace-only string raises `LLMMalformedResponse`; `LLMTimeout`
  propagates.
- `test_context.py` — trait ordering by strength rank → updated_at;
  thread / entity zero-evidence filtering; descending by count;
  cross-person isolation; archived rows excluded; limits respected;
  empty sections omitted from rendered context.
- `test_time_period.py` — year range from `time_anchor.year`;
  life-period chronology ordering; mixed year + period case;
  unknown periods sort alphabetically after known; superseded
  moments excluded; chronology table drift detector.
- `test_persistence.py` — UPDATE writes summary + bumps
  `updated_at`; idempotency row records correct char count;
  transaction atomicity (failure rolls back both writes); fresh
  summaries overwrite stale ones.
- `test_idempotency.py` — `make_runonce_key` shape + uniqueness;
  `is_processed` true after `mark_processed`; ON CONFLICT DO NOTHING
  preserves original; `mark_processed_empty` writes chars=0; works
  through cursor or connection.
- `test_runner.py` — happy path persists summary + idempotency row;
  same key short-circuits the second run; different keys overwrite
  each other; empty-legacy short-circuit (no LLM call, idempotency
  row with chars=0); empty-then-populated produces a real summary.
- `test_worker.py` — happy path acks; `LLMTimeout` does NOT ack;
  `LLMMalformedResponse` and base `LLMError` ack (fail-soft);
  generic exceptions do NOT ack; redelivery of the same MessageId
  skips and acks both; fresh summary overwrites stale one;
  empty-legacy ack without LLM call.

### Configuration

`src/flashback/config.py` adds:

- `ProfileSummaryConfig` — full env block including
  `PROFILE_SUMMARY_QUEUE_URL`, the `LLM_PROFILE_SUMMARY_*` family
  (defaults to `LLM_BIG_*`, i.e. Sonnet), and the three top-N caps
  (`PROFILE_SUMMARY_TOP_TRAITS_MAX=7`,
  `PROFILE_SUMMARY_TOP_THREADS_MAX=5`,
  `PROFILE_SUMMARY_TOP_ENTITIES_MAX=8`). `from_env(queue_required=False)`
  lets the `run-once` CLI path skip the queue URL for ad-hoc testing.

`.env.example` — new section listing the env vars added by this step.

`pyproject.toml` — no new top-level dependencies. The generator uses
what's already in the tree: pydantic, structlog, psycopg, boto3, plus
the existing `call_text()` async LLM interface.

## How it works

### The five inputs

The LLM is given a single user message assembled from five context
blocks. Empty blocks are omitted entirely so the model doesn't see
"(none)" filler:

1. **Subject** — name + relationship to the contributor.
2. **Time period** — year range (min, max of `time_anchor.year` across
   active moments) and/or distinct `life_period_estimate` strings,
   ordered by approximate chronology.
3. **Top traits** — up to 7, ordered by strength rank descending
   (`defining=4 > strong=3 > moderate=2 > mentioned_once=1`) then
   `updated_at` desc as the tiebreaker.
4. **Top threads** — up to 5, ordered by count of active `evidences`
   edges from active moments, then `created_at` desc. Threads with
   zero evidencing active moments are filtered out.
5. **Top entities** — up to 8, ordered by count of active `involves`
   edges from active moments, then `created_at` desc. Entities with
   zero active-moment mentions are filtered out.

Caps are configurable via env (see `.env.example`).

### Time period derivation (code, not LLM)

`time_period.py` reads `(time_anchor->>'year')::int` and
`life_period_estimate` from `active_moments` for the person and
returns a `TimePeriodView`:

- `year_range`: `(min, max)` of non-null years; `None` if no active
  moments have a year anchor.
- `life_periods`: distinct non-empty values sorted by
  `LIFE_PERIOD_ORDER` (childhood → late life), with unknown strings
  appended alphabetically.

The chronology table:

```python
LIFE_PERIOD_ORDER = (
    "childhood", "youth", "young adult", "early career", "career",
    "parenthood", "midlife", "later years", "retirement", "late life",
)
```

This is best-effort. If extraction starts producing different period
labels we'll refine the table; unknown labels degrade gracefully
(they sort to the end alphabetically).

### Single big-LLM call

One Sonnet call per person via `call_text()` (no tool use). The
returned string IS the summary; the wrapper strips whitespace and
treats empty / whitespace-only output as
`LLMMalformedResponse` (a permanent error — see below).

Token budget `max_tokens=600` (summaries are ~150-300 words, so 600
leaves ample headroom). Hard timeout 30s; this is a background job,
not a user-facing call.

### Empty-legacy short-circuit

If the person has zero traits AND zero threads AND zero entities,
`run_once` skips the LLM call entirely, writes only an idempotency
row with `summary_chars=0`, and returns
`RunResult.empty_legacy()`. Reasoning: there is nothing to
summarize, and "writing a paragraph about a stub" is exactly the
kind of grief-tech anti-pattern the spec calls out.

The idempotency row still gets written so a redelivery doesn't
repeat the no-op. The next session that adds *any* trait, thread, or
entity will produce a real summary on its key.

### CLI `run-once` for ops/testing

```bash
python -m flashback.workers.profile_summary run            # drain SQS
python -m flashback.workers.profile_summary run-once \
    --person-id <uuid>                                     # one-shot
```

`run-once` shares the entire `runner.run_once` codepath with the
queue worker. Its only differences are (a) it builds an idempotency
key locally via `make_runonce_key()` (`runonce-{person_id}-{ms}`)
and (b) it doesn't talk to SQS. Idempotency is best-effort on this
path — same person twice in rapid succession produces two distinct
keys, two rows in `processed_profile_summaries`, and two LLM calls.
Each call overwrites the previous summary on `persons.profile_summary`,
which is the desired behavior.

### Fail-soft policy on permanent vs. transient LLM errors

Same as step 13. The exception hierarchy is `LLMError` ←
`LLMTimeout`, `LLMMalformedResponse`. The worker catches the more
specific `LLMTimeout` first (do NOT ack — let SQS redrive on the
visibility timeout), then `LLMError` afterwards (ack — covers both
`LLMMalformedResponse` and any other permanent provider error). A
generic `Exception` does NOT ack (programmer error or DB outage —
SQS will redrive).

The fail-soft on permanent LLM errors is deliberate: profile
summaries are *display* artifacts, not load-bearing. Better to drop
one summary than block the queue head on a malformed response. The
next session's wrap will trigger a new attempt anyway.

### Per-person transaction

Persistence runs in one transaction:

1. UPDATE `persons.profile_summary` (and bump `updated_at`).
2. INSERT into `processed_profile_summaries` for idempotency.

If anything raises mid-way, the surrounding transaction rolls back
and the SQS message is not acked. The idempotency row is written
inside the transaction, so persisted-state and idempotency-state
move together.

### No embeddings, no history

Profile summaries are display only. The `persons` table has no
`profile_summary_embedding` column, and we don't push to the
embedding queue here. There's also no history table — overwrites are
fine; nothing load-bearing is lost (the source data — moments,
threads, entities, traits — persists).

### Idempotency at the worker, not the DB

Same MessageId twice → second is a skip and an ack. Different keys
for the same person each run independently and produce a fresh
summary that overwrites the previous one. This is the desired
shape: profile summaries get fresher as more is recorded.

## Verified

- ✅ `python -m pytest tests/workers/profile_summary` against a
  live test database (`TEST_DATABASE_URL` set) — all tests pass.
- ✅ Migration `0006` applies via the `schema_applied` test fixture
  (which picks up every `*.up.sql`) and rolls back cleanly via the
  symmetric `0006_profile_summary_support.down.sql`.
- ✅ `python -m flashback.workers.profile_summary --help` prints
  the two-subcommand parser; `run` and `run-once` are wired through
  `ProfileSummaryConfig.from_env` and open the pool / SQS clients
  exactly like the sibling workers.
- ✅ End-to-end happy path in `test_worker.py`:
  - Seeded a person with three traits, two threads, two entities,
    three moments, plus the appropriate `evidences` and `involves`
    edges; pushed one message.
  - `call_text` stub returned a one-line prose summary.
  - Asserted: `persons.profile_summary` populated; idempotency row
    written; SQS message acked.
- ✅ Empty-legacy short-circuit: a person with no traits/threads/
  entities → no LLM call (test asserts the stub is never invoked),
  no summary written, idempotency row recorded with `chars=0`.
- ✅ Failure modes: `LLMTimeout` → no ack; `LLMMalformedResponse`
  and base `LLMError` → ack; generic `Exception` → no ack;
  redelivery of the same MessageId → skip + ack.

## Out of scope (deferred)

- **Producers P2 / P3 / P5.** Step 15.
- **Session Wrap.** Step 16. The `profile_summary` queue producer
  doesn't exist yet; the worker consumes from a queue that's fed by
  `run-once` for testing only. The `ProfileSummaryJobSender` class
  is shipped here so step 16 has a producer to import.
- **Profile summary localization.** All en-US.
- **Profile summary embedding.** Not stored as a vector.
- **Profile summary versioning / history.** Overwrites are fine.
- **Profile summary regeneration on schema changes.** If we reshape
  the input format later, existing summaries stay until the next
  session triggers a fresh one.

## Deviations from the prompt and edge cases worth flagging

1. **Repo-relative paths.** The prompt referenced
   `src/workers/profile_summary/...` and `src/config.py`. The actual
   layout (matching the rest of the repo) puts these under
   `src/flashback/workers/profile_summary/...` and
   `src/flashback/config.py`. Using the real paths.

2. **psycopg pool API.** The prompt's pseudocode used
   `with db.transaction() as tx:`. The real pattern in this repo
   (and what psycopg v3 + `psycopg_pool.ConnectionPool` actually
   expose) is the three-step
   `with db_pool.connection() as conn: with conn.transaction(): with conn.cursor() as cur:`.
   Followed throughout, mirroring the extraction, thread_detector,
   and trait_synthesizer workers.

3. **`mark_processed_empty` helper.** The prompt's `runner.py`
   pseudocode called `mark_processed(db_pool, key, person_id, ...)`
   while the persistence path passes a `cursor`. To support both
   call shapes cleanly, the empty-legacy path uses a small
   `mark_processed_empty(db_pool, ...)` helper that opens its own
   short transaction. The cursor-based `mark_processed(...)` is the
   one used inside the persistence transaction.

4. **`RunResult.empty_legacy()` distinct from `RunResult.skip()`.**
   The prompt's pseudocode mixed "skipped" and "empty" outcomes; the
   implementation distinguishes them so the worker log line and the
   tests can tell which path fired. `skipped=True` means
   "idempotency hit"; `empty=True` means "empty legacy
   short-circuit". Both ack on the worker; both write at most one
   idempotency row.

5. **`ProfileSummaryMessage` is the inbound payload schema.**
   Mirrors `TraitSynthMessage` — a single `person_id` field with
   `extra="ignore"` so future producer revisions can add fields
   without breaking redelivery of older messages.

6. **No `test_schema.py`.** The `ProfileSummaryMessage` is a
   one-field model; there's nothing meaningful to drift-test beyond
   what the integration tests already exercise. The prompt's test
   list didn't require it. Skipped to avoid trivia tests.
