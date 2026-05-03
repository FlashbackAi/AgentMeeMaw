# Step 1 — Initial Schema

This bundle is the foundation of the agent service: the canonical graph
schema, the edge validator, and the schema reference doc.

## Contents

```
step-01/
├── README.md                                    (this file)
├── SCHEMA.md                                    → goes to repo root
├── migrations/
│   ├── 0001_initial_schema.up.sql               → migrations/
│   └── 0001_initial_schema.down.sql             → migrations/
└── src/
    └── db/
        ├── __init__.py                          → src/db/
        └── edges.py                             → src/db/
```

## What it creates

- **8 tables** — `persons`, `moments`, `entities`, `threads`, `traits`,
  `questions`, `edges`, `moment_history`.
- **7 active views** — one per node table plus `active_edges`.
- **28 indexes**, including HNSW on every embedded row (partial,
  filtered to `status='active'`).
- **`trg_set_updated_at`** trigger function applied to every audited
  table.
- **Constraints** that enforce the architecture invariants:
  - `phase`, `status`, `kind`, `source`, `strength`, `edge_type`
    membership.
  - Embedding completeness (vector and `embedding_model` /
    `embedding_model_version` move together).
  - Question scope (`starter_anchor` ⇔ NULL `person_id`).
  - Edge uniqueness `(from_kind, from_id, to_kind, to_id, edge_type)`.

## What it deliberately doesn't do

- **No FKs on the `edges` table.** Heterogeneous targets — validation
  is in `src/db/edges.py` instead.
- **No starter question seed data.** That's step 2.
- **No application code beyond `validate_edge()`.** ORM / connection
  layer / model classes come with the workers that need them.
- **No history tables for entities / threads / traits.** Only
  `moment_history` for v1.

## Applying the migration

The SQL is plain Postgres + pgvector. Apply however you prefer.

### Quick: psql

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/0001_initial_schema.up.sql
```

### Rollback

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/0001_initial_schema.down.sql
```

### Wiring to a tool

If you adopt Alembic, yoyo-migrations, dbmate, or similar, they all
support raw SQL files. The migration is one transactional script, so
single-file wrappers work cleanly.

## Requirements

- Postgres 13+ (for `gen_random_uuid()` in pgcrypto).
- `pgvector` extension installed on the server.
- A role with `CREATE` on the database. The migration installs
  extensions itself (`CREATE EXTENSION IF NOT EXISTS`) — that requires
  superuser or the `CREATE` privilege on the database depending on
  your Postgres version. If your DB is locked down, run the two
  `CREATE EXTENSION` lines as superuser first and the rest as the
  app role.

## Verified

Applied cleanly against Postgres 16 with pgvector 0.6.0. Constraints
exercised:
- phase CHECK rejects invalid values
- embedding completeness CHECK rejects partial state
- question scope CHECK enforces both directions
- edge UNIQUE rejects duplicates
- updated_at trigger fires on UPDATE
- DOWN migration cleans up everything

## Next: step 2 — starter question seed

Step 2 will add a second migration (`0002_seed_starter_questions.sql`)
that inserts ~15 `starter_anchor` template rows: 5 dimensions × 2–3
phrasings each, with `attributes.dimension` and `attributes.themes`
populated. The Phase Gate uses these via the
`questions_starter_dimension_idx` partial index added here.
