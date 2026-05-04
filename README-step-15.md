# README-step-15 - Question Producers P2/P3/P5

Step 15 adds the remaining steady-phase question producers:

| Producer | Source | Cadence | Queue |
|---|---|---|---|
| P2 | `underdeveloped_entity` | per session | `producers_per_session` |
| P3 | `life_period_gap` | weekly | `producers_weekly` |
| P5 | `universal_dimension` | weekly | `producers_weekly` |

P1 remains inline in the Extraction Worker, P4 remains inline in the
Thread Detector, and P0 remains the starter-question seed migration.

## What changed

New migration:

- `migrations/0007_question_producers_support.up.sql`
- `migrations/0007_question_producers_support.down.sql`

New worker package:

- `flashback.workers.producers.__main__`
- `worker.py` for queue ack/no-ack policy
- `runner.py` for idempotent per-person dispatch
- `underdeveloped.py` for P2
- `life_period.py` for P3
- `universal.py` for P5
- `prompts.py`, `schema.py`, `protocol.py`, `persistence.py`,
  `idempotency.py`, `sqs_client.py`

`src/flashback/config.py` now has `ProducerConfig`, and `.env.example`
lists the two producer queues plus LLM and cap settings.

## Detection Rules

P2 finds active entities for the person with fewer than 3 active moment
mentions. It orders them by the lowest mention count and then the
shortest description, caps the batch with `P2_MAX_ENTITIES_PER_RUN`,
and includes related thread names as LLM context. Persisted P2 questions
write a `question -> entity` `targets` edge.

P3 first looks for year anchors in active moments and computes missing
decade buckets between the minimum and maximum represented decade. If
there are no year anchors, it falls back to
`profile_summary.time_period.LIFE_PERIOD_ORDER` and finds missing
`life_period_estimate` buckets. Persisted P3 questions store
`attributes.life_period` and write no edges.

P5 walks `UNIVERSAL_DIMENSIONS` and counts active moments plus active
threads whose text contains any configured keyword for that dimension.
Dimensions with count below `P5_DIMENSION_COVERAGE_THRESHOLD` are
under-covered, ordered by lowest coverage, and capped by
`P5_MAX_DIMENSIONS_PER_RUN`. Persisted P5 questions store
`attributes.dimension` and write no edges.

The P5 keyword map is deliberately a v1 heuristic. It should be tuned
against real Extraction Worker output. Future refinements could tag
moments with universal dimensions during extraction or use a small
classifier.

## Question Bank Growth

Each producer makes a single small-LLM tool call per person per run.
The runner persists all questions from that run in one transaction,
marks `processed_producer_runs`, then pushes one embedding job per new
question after commit.

Every question stores `attributes.themes`; the Pydantic result model and
tool schemas require at least one theme per question. Universal-dimension
ranking caps are still enforced by Phase Gate, not by this producer.

## Fail-Soft Policy

The producer worker follows the step 13/14 policy:

- `LLMTimeout`: no ack; let SQS redrive.
- `LLMError` / malformed tool call: ack and mark the run processed with
  zero questions so the same message does not loop forever.
- generic exception: no ack; let SQS redrive.

## Commands

```bash
python -m flashback.workers.producers run-per-session
python -m flashback.workers.producers run-weekly
python -m flashback.workers.producers run-once --producer P2 --person-id <uuid>
python -m flashback.workers.producers run-once --producer P3 --person-id <uuid>
python -m flashback.workers.producers run-once --producer P5 --person-id <uuid>
```

`run-per-session` requires `PRODUCERS_PER_SESSION_QUEUE_URL`.
`run-weekly` requires `PRODUCERS_WEEKLY_QUEUE_URL`.
`run-once` does not require either producer queue URL, but it still
requires `EMBEDDING_QUEUE_URL`.

## Verified

- [x] `python -m compileall src\flashback\workers\producers`
- [x] `python -m flashback.workers.producers --help`
- [x] `python -m pytest tests\workers\producers\test_prompts.py -q`
- [x] `python -m pytest tests\workers\producers -q` - 30 passed
- [x] `python -m pytest` - 481 passed
