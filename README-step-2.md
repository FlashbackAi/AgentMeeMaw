# Step 2 — Starter Question Seed

This bundle adds Producer 0's output (the starter anchor questions)
plus the editorial reference doc for everything question-related.

## Contents

```
step-02/
├── README.md                                    (this file)
├── QUESTION_BANK.md                             → goes to repo root
└── migrations/
    ├── 0002_seed_starter_questions.up.sql       → migrations/
    └── 0002_seed_starter_questions.down.sql     → migrations/
```

## What it creates

15 rows in the `questions` table:
- 5 anchor dimensions (`sensory`, `voice`, `place`, `relation`, `era`)
- 3 phrasings each
- All `source = 'starter_anchor'`, `person_id IS NULL` (global
  templates)
- `attributes.dimension` and `attributes.themes` populated on every
  row
- Embedding columns left NULL — backfilled by the embedding worker in
  step 3

## What's verified

Applied against Postgres 16 + pgvector 0.6.0 on top of step 1:

- ✓ `INSERT 0 15` (15 rows)
- ✓ Count by dimension: 3 each across all 5 dimensions
- ✓ All rows are templates (`person_id IS NULL`)
- ✓ All rows have non-empty `themes`
- ✓ All embedding columns NULL (correct — embedding worker fills
  these)
- ✓ Phase Gate's partial index (`questions_starter_dimension_idx`) is
  used by the dimension-lookup query (`Index Scan`, not `Seq Scan`)
- ✓ Down migration cleanly removes all 15 rows and any referencing
  edges

## Applying

```bash
# Up
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
    -f migrations/0002_seed_starter_questions.up.sql

# Down (only the seeds; doesn't touch step-1 schema)
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
    -f migrations/0002_seed_starter_questions.down.sql
```

**Idempotency:** the `.up.sql` uses plain `INSERT`, not `ON CONFLICT`.
Running it twice will create duplicates. To reseed cleanly, run
`.down.sql` first.

## Editorial rationale

Why these specific 15 questions and not others — see
`QUESTION_BANK.md` §4.1 for the editorial principles and §4.2–§4.6 for
the per-question rationale.

The short version:

- **Concrete over abstract** — "What's a smell..." not "What did they
  smell like?"
- **First turn is always sensory** — bypasses narrative framing
- **No DOB/DOD probing** — lifespan emerges from anchored stories
- **No superlatives** ("favorite," "best," "most") — they ask
  contributors to evaluate; we want them to recall
- **Three phrasings per dimension**, not five — wording quality beats
  variant count at this scale

## Next: step 3 — embedding worker

Step 3 will be the first **Claude Code prompt**, per the new working
mode. The prompt will produce:
- The embedding worker that drains the `embedding` SQS queue
- Voyage AI integration with `model` + `version` written in lockstep
  with the vector
- The version-guarded UPDATE that prevents stomping a row whose model
  was upgraded mid-flight
- Initial backfill behavior for NULL embeddings (which will pick up
  the 15 starter rows seeded here)
