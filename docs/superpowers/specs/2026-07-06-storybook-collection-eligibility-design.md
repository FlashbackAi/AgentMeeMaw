# Storybook collection eligibility — extraction-time tagging + deterministic gate

**Date:** 2026-07-06
**Status:** Approved (design), pending implementation plan

## 1. Problem

A storybook can be minted for a collection that none of the person's
moments actually fit. The mint gate is pool-wide
(`STORYBOOK_MIN_MOMENTS = 3` qualifying moments of *any* kind), while
collection fit is decided later by the curation LLM **in the worker**,
after the row is inserted and the job enqueued. Worse,
`select_moments` (worker.py) falls back to the whole unrelated pool
when curation returns nothing for the requested collection ("a short
book beats no book") — so an "Adventures" book for a subject with only
family-dinner moments hands the assembler the entire pool and instructs
it to write 7 pages in the adventure register. Hallucination by
construction.

Even without the fallback, curation is allowed to return 1–2 weak fits,
and the assembler must still fill exactly `PAGE_COUNT = 7` pages (one
scene per page), so thin slices force stretching or invention.

## 2. Decisions (user-approved)

| Decision | Choice |
|---|---|
| Eligibility source | Extraction-time collection tagging (schema change), chosen over route-side cached curation — accuracy over ship speed |
| Per-collection floor | **5** tagged moments (grid collections) |
| Tag shape | **Multi-label** — a moment may fit several collections; assembly becomes deterministic and the curation LLM pass is retired |
| `wisdom` | **Not tagged, not per-collection gated** — stays a lens over the whole qualifying pool, floor 3 (existing behavior) |
| Backfill | One-time admin script tagging all existing active moments, run at deploy |

## 3. Schema (migration 0036)

```sql
ALTER TABLE moments
  ADD COLUMN storybook_collections TEXT[] NULL;
CREATE INDEX idx_moments_storybook_collections
  ON moments USING GIN (storybook_collections);
```

- `NULL` = never tagged (rows extracted before this feature).
- `'{}'` = tagged, fits no collection.
- The NULL/empty distinction is load-bearing: the backfill selects
  `WHERE storybook_collections IS NULL`, and monitoring can tell
  "unprocessed" from "genuinely fits nothing".
- No new tables, no edges. Collections are a fixed 6-slug Python
  registry (`flashback.storybook.collections`), not graph nodes —
  unlike themes they have no per-person rows, so a `TEXT[]` column is
  the right shape.
- Down migration drops the index + column.

## 4. Extraction tagging (the accuracy layer)

- `MomentOut` in `flashback/workers/extraction/schema.py` gains
  `collections: list[str] = Field(default_factory=list)`, exactly
  parallel to the existing `themes` field.
- The extraction prompt renders a `<collection_catalog>` block listing
  each **grid** slug (`childhood`, `interesting`, `nostalgia`,
  `festivals`, `adventurous`) with its `theme_focus` description from
  the collections manifest. `wisdom` is deliberately absent.
- Prompt rules: tag only genuine fits; an empty list is expected and
  fine; never stretch a moment into a collection to fill it (mirrors
  invariant #6's under-extraction ethos).
- Persistence writes the array onto the moments row in the existing
  insert; unknown slugs are dropped silently (invariant #6). The
  supersession/refinement path naturally re-acquires tags from the
  fresh LLM emission because the new moment row carries its own array
  (no edge cleanup needed — it's a column, not an edge).
- No new LLM call: this rides the existing big-LLM (Sonnet) extraction
  call, which has full conversation context — that is where tagging
  accuracy comes from.

## 5. The gate (route-side, deterministic SQL)

- New constant `STORYBOOK_COLLECTION_FLOOR = 5` in
  `flashback/storybook/repository.py`.
- `fetch_scope_scene_moments_async` gains a `collection` scope: for
  grid slugs the qualifying predicate becomes the existing
  `_QUALIFYING` **AND** `%(collection)s = ANY(m.storybook_collections)`.
  For `wisdom` (and no-collection callers) the query is unchanged.
- `build_preview` and `generate_storybook` gate on the scoped pool:
  grid collections need ≥ 5, `wisdom` keeps the pool-wide ≥ 3. Under
  the floor → `StorybookTooThin` → **409** — nothing inserted, nothing
  enqueued. `StorybookTooThin` is parametrized with the applicable
  floor so the message states the real requirement.
- `effective_min_select` for grid collections keys off
  `STORYBOOK_COLLECTION_FLOOR` (a pool of exactly 5 must allow
  selecting 5); wisdom keeps the existing relaxation.
- `GET /storybook-collections` gains an optional `person_id` query
  param. When present, each entry additionally carries
  `tagged_count` (scoped qualifying count; whole-pool count for
  wisdom) and `eligible: bool`. Node's chooser renders locked cards
  with "3/5 stories" badges — the same affordance as theme lock cards.
  Without `person_id` the response is unchanged (registry only).

## 6. Curation LLM retired; deterministic assembly

- A grid collection's book slice **is** its tagged qualifying pool:
  1. ordered life-chronologically (existing `_CHRONO_ORDER`),
  2. moments already used in another completed book demoted to the
     back (existing `fetch_storybook_usage_async` data) — cross-book
     repetition control replacing curation's single-assignment rule,
  3. capped at `STORYBOOK_MAX_SELECT` (25).
- The route resolves this definitive slice on **every** path (auto and
  user-picked) and writes it into `scene_moment_ids` + the render
  context. Postgres stays authoritative (§3 hard rule); the worker
  never chooses content again.
- **Deleted:** `flashback/storybook/curation.py`,
  `flashback/storybook/curation_cache.py`, the worker's
  `curate_moments` call, and the `or ctx.moments` whole-pool fallback
  in `select_moments`. The worker's job shrinks to: assemble script
  from `ctx.moments` (or reuse stored script) → refs → render →
  upload. One less LLM call per book, and the gate can never disagree
  with the content.
- Preview payload: `picked` = the resolved slice; each moment gains a
  `collections` field (its tags). The existing `suggested_collection`
  field remains populated (first tag other than the requested
  collection, else null) for Node compatibility, documented as
  deprecated.
- Regenerate / edit (`_rerender`): the slice re-resolves from tags (or
  the stored user-confirmed selection). If supersession has gutted it
  below the applicable floor, the request **409s**
  (`StorybookTooThin`) instead of today's silent fallback to the full
  pool — a stranded edit is recoverable; a hallucinated book is not.

## 7. Backfill (one-time admin script)

- `scripts/backfill_storybook_collections.py`:
  - Iterates persons; loads active moments
    `WHERE storybook_collections IS NULL` in batches.
  - Tags each batch with a dedicated tool-call on the big-LLM model
    (accuracy parity with extraction) using the same
    `<collection_catalog>` + rules as the extraction prompt, and
    writes arrays back (`'{}'` when the
    LLM returns no fits — so re-runs skip it).
  - Idempotent: re-run picks up only NULL rows. Failures leave rows
    NULL for the next run.
- Run once at deploy so existing prod legacies immediately show true
  per-collection eligibility.

## 8. Node-side impact (contract only — separate repo)

- Chooser: call `GET /storybook-collections?person_id=...`; render
  locked/eligible per collection with `tagged_count`/floor badges.
- Handle 409 on `POST /storybooks/preview` and `POST /storybooks` for
  under-floor collections (message text is user-presentable).
- Preview: `suggested_collection` deprecated in favour of per-moment
  `collections`.
- Documented in `API.md` + `NODE_INTEGRATION.md` as part of this work.

## 9. Testing

- Migration 0036 up/down (`tests/db/`).
- Extraction: schema accepts/defaults `collections`; unknown slugs
  dropped; persistence writes the array; supersession re-tags.
- Gate: preview + generate + regen + edit thresholds per collection
  kind (grid 5 / wisdom 3); NULL vs `'{}'` semantics; 409 mapping.
- Slice resolution: chrono order, used-in demotion, 25 cap,
  `scene_moment_ids` written on the auto path.
- Worker: no curation call; renders exactly `ctx.moments`; reuse-script
  path untouched.
- Retired with their code: `tests/storybook/test_curation.py`,
  `tests/storybook/test_curation_cache.py`.
- Manual verify against the local dev stack (`/verify` flow).

## 10. Out of scope

- Variable page count / shorter books (Node presigned-URL contract
  pins `PAGE_COUNT = 7`).
- Tagging `wisdom` or gating it per-collection.
- Re-tagging existing moments when the collections registry changes
  (manual backfill re-run is the recovery path, matching the
  ground-truth "read-at-compose-time" ethos).
