# Storybook user-curated moments — design

**Date:** 2026-07-05
**Status:** Approved (pending implementation)
**Builds on:** `2026-06-29-storybooks-python-render-design.md`

## Problem

Storybook creation is fully automatic today: `POST /storybooks` snapshots
the whole qualifying pool into the render context and the worker's Sonnet
curation pass picks each collection's slice invisibly. The user never sees
which moments were chosen and cannot influence the selection. We want a
before-render review step: show the picked moments, let the user exclude
and add, then render from the confirmed set.

## Decisions (from brainstorming)

- **Before-render preview, optional/skippable.** `POST /storybooks`
  without the new field behaves exactly as today — Node can ship the UI
  incrementally and the old flow keeps working.
- **User is king.** Any qualifying moment is addable, including ones
  curation assigned to a different collection. A moment already used by
  another *rendered* book gets an "also appears in X" warning chip —
  informational, never blocking.
- **All six collections.** The five curated grid collections show
  curation's picks pre-selected; `wisdom` shows the whole pool as
  included and the user excludes (no curation call on that path).
- **Scope: exclude/add only.** No per-pick reasons, no draft resume, no
  cover-moment selection this round.
- **Bounds: min 5 / max 25**, with the min relaxed to the existing pool
  floor of 3 when the person's whole qualifying pool is under 5.
- **Selection is a set.** The script assembler owns narrative order (it
  already sequences scenes on the subject's timeline for age
  consistency); the preview is a checklist, not a sortable list.
- **Curation cached per pool snapshot** (fingerprint of the qualifying
  moment ids). Opening a second collection's preview is instant and
  picks stay consistent across books; new extracted moments change the
  fingerprint and self-invalidate the cache.

## Architecture

### 1. Preview endpoint

`POST /storybooks/preview` — body `{person_id, collection}`,
service-token protected like every route in `routes/storybooks.py`.

Flow:

1. Validate the collection slug (400 `UnknownCollection` otherwise).
2. Fetch the qualifying pool via the existing
   `fetch_scope_scene_moments_async` (same `STORYBOOK_CANDIDATE_LIMIT`
   cap of 40). 404 if the person does not exist; 409
   `StorybookTooThin` if the pool is under `STORYBOOK_MIN_MOMENTS` (3).
3. Grid collection → get the curation assignment (cache, §2).
   `wisdom` → skip curation entirely; every moment is `picked: true`.
4. Compute `used_in` per moment: one query over the person's other
   **active** storybooks' `scene_moment_ids`, mapped moment id →
   `[collection]`. Best-effort chip data; an empty result never blocks.

Response:

```json
{
  "collection": "adventures",
  "bounds": {"min_select": 5, "max_select": 25},
  "moments": [
    {
      "id": "<uuid>",
      "title": "...",
      "snippet": "first ~200 chars of narrative",
      "life_period": "...",
      "picked": true,
      "suggested_collection": "adventures",
      "used_in": ["family_kitchen"]
    }
  ]
}
```

Ordering: picked moments first in curation rank order, then the rest of
the pool. `suggested_collection` is the grid slug curation assigned the
moment to (`null` if unassigned); on the `wisdom` preview it is `null`
for every moment (no curation ran). `bounds.min_select` is 5, or 3 when
`pool_count < 5`.

### 2. Curation cache (Valkey, fingerprinted)

- Key: `storybook_curation:{person_id}`.
- Value: `{fingerprint, assignments}` where `fingerprint` is the sha256
  of the sorted qualifying moment ids and `assignments` maps grid slug →
  ordered list of **moment ids** (ids, not pool indices, so a cached
  assignment survives pool reordering between calls).
- Cache-aside, mirroring the entity-name cache pattern (invariant #20):
  fingerprint the fetched pool → cached fingerprint matches → reuse;
  else run the existing `curate_moments` one-pass call (it already
  assigns all five grid collections at once) and overwrite.
- Valkey down / flushed / miss = one extra Sonnet call. Nothing breaks,
  nothing persists that can't be recomputed (invariant #7).
- **No DEL hook in the Extraction Worker.** New moments change the
  fingerprint, which self-invalidates — no new coupling.

### 3. Confirm — `POST /storybooks` grows optional `moment_ids`

`moment_ids: list[UUID] | null` on `StorybookGenerateRequest`.

- **Absent → today's auto-curate path, byte-for-byte.** This is the
  backward-compatibility contract.
- **Present → validation, in order:**
  1. Dedupe, then resolve every id against the qualifying pool fetched
     for that person (pool membership, not merely "active" — the same
     pool the preview showed; person-scoping per invariant #2 falls out
     of this). Any unknown / non-qualifying id → 400
     (`unknown or non-qualifying moment ids`).
  2. Bounds after resolution: under the effective min or over 25 → 409
     with a human-readable message.
  3. Build the context from **only the confirmed slice**, each moment
     payload now carrying `id`, plus a top-level `user_curated: true`
     flag. Seed the row's `scene_moment_ids` with the confirmed ids at
     insert (today it is `[]` until the worker writes).
- `moments_count` in the response and on the row = confirmed count.

### 4. Worker changes (surgical)

- `StorybookRenderContext` gains `user_curated: bool = False`; the
  per-moment payload gains `id`. Both **default** so any already-queued
  context (mid-deploy) deserializes unchanged.
- `_curate_and_assemble` gains one branch: `ctx.user_curated` → skip
  `curate_moments` and use `ctx.moments` as-is — exactly the existing
  `wisdom` path.

### 5. Rerender preserves the selection (the trap)

`_rerender` today re-fetches the **whole pool** and rebuilds the
context — an edit on a user-curated book would silently hand the worker
all 40 moments and let it re-curate, discarding the user's picks.

Fix: `_rerender` reads the stored context's `user_curated` flag and
moment ids and, when set, rebuilds the slice via the existing
order-preserving `fetch_moments_by_ids_async` (superseded ids fall out
naturally) and re-marks the new context `user_curated`. If supersession
drops the slice below the absolute floor of 3, **fall back to the
full-pool auto path with a warning log** rather than 409 — there is no
post-render re-pick surface this round, so an error would strand the
user's edit. `regenerate` (reuse_script=true) does not re-assemble the
script, but gets the same context treatment so a later
missing-saved-script fallback stays consistent.

### 6. Contract + docs

- `API.md`: preview endpoint shape + the `moment_ids` field + error
  codes (400 unknown ids / bad collection, 409 bounds / too thin).
- `NODE_INTEGRATION.md`: preview-before-create sequence, chip rendering
  (`suggested_collection`, `used_in`), UI-side bounds enforcement, and
  that `moment_ids` is optional.
- `CLAUDE.md` §9: one line for the preview endpoint and the new field.

## Error handling summary

| Case | Response |
|---|---|
| Unknown collection slug (preview or confirm) | 400 |
| Person not found | 404 |
| Pool under 3 (preview or confirm) | 409 `StorybookTooThin` |
| `moment_ids` containing non-pool ids | 400 |
| Confirmed selection under effective min / over 25 | 409 |
| Valkey unavailable at preview | re-curate inline (slow, correct) |
| Curation LLM failure at preview | 502-equivalent LLMError surfaced; no cache write |
| Superseded ids at rerender dropping slice < 3 | full-pool auto fallback + warning log |

## Testing

- Preview route: cache miss (curation runs, cache written), cache hit
  (no LLM call), fingerprint change re-curates, wisdom path makes no
  LLM call, `used_in` mapping, thin-pool 409, unknown collection 400.
- Confirm: non-pool ids 400, bounds 409 (both edges + relaxed min),
  dedupe, `scene_moment_ids` seeded, context carries `user_curated` +
  ids, absent `moment_ids` produces today's context exactly.
- Worker: `user_curated` skips curation; old-shape context (no new
  keys) deserializes and renders via the auto path.
- Rerender: edit on a user-curated book rebuilds the confirmed slice;
  superseded ids fall out; slice < 3 falls back to full pool with the
  warning log.
- Existing auto-path tests pass untouched — the backward-compat proof.

## Out of scope (explicitly deferred)

- Post-render moment swapping / re-pick surface.
- Per-pick "why we chose this" reasons (curation tool schema change).
- Draft resume for an abandoned preview.
- Cover-moment selection.
- Any Node/frontend implementation (separate repo; contract only).
