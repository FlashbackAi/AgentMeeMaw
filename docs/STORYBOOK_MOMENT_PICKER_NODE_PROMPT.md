# Node Prompt — Storybook moment picker (preview → confirm with `moment_ids`)

**For:** the Node Backend + frontend team.
**Status:** agent side built and merged to `main` (spec
`docs/superpowers/specs/2026-07-05-storybook-user-curation-design.md`;
`API.md` §7c, `NODE_INTEGRATION.md` §7c step 1b, `CLAUDE.md` §9 updated).
**No migration, no new queue, no NOTIFY changes.** Node work is one new
agent call + a checklist UI + one optional field on the create call.

---

## TL;DR

Storybook creation gets an **optional review step**: before a book renders,
the user sees **which moments the curator picked** for that collection, can
**exclude** any of them and **add** any other qualifying moment, then
confirms. The whole feature is two agent touchpoints:

1. **`POST /storybooks/preview`** — returns the picks (pre-selected) + the
   rest of the pool, with chip metadata and selection bounds. Read-only,
   stateless: nothing is minted, persisted, or enqueued.
2. **`POST /storybooks`** gains optional **`moment_ids: uuid[]`** — the
   confirmed selection. **Omit it and the old auto-curate flow is
   byte-for-byte unchanged**, so you can ship this incrementally (or put it
   behind a flag) with zero risk to the existing path.

The render pipeline, presigned-URL handshake, LISTEN/NOTIFY completion, and
regenerate/edit calls are **all unchanged on the wire**. (Internally the
agent now preserves a user-confirmed slice across regenerate/edit — you do
nothing for that.)

---

## 1. The flow

```
user taps a collection card
  →  POST /storybooks/preview {person_id, collection}     ← BEFORE minting URLs
  →  render checklist: picked moments pre-selected on top, rest below
  →  user toggles excludes/adds (enforce bounds client-side)
  →  user confirms → mint storybook_id + presigned URLs (unchanged, §3 of
     STORYBOOK_PYTHON_NODE_PROMPT.md)
  →  POST /storybooks {..., moment_ids: [confirmed ids]}
  →  everything downstream identical (NOTIFY, URL writes, gallery)
```

Call the preview **before** minting anything — it's free to abandon. A
"Looks good" tap with no edits should still send the picked ids as
`moment_ids` (what the user saw is what they approved). A "Skip" affordance
(or the feature flag being off) just omits `moment_ids`.

## 2. `POST /storybooks/preview`

Request: `{ "person_id": "uuid", "collection": "childhood" }`

Response `200`:

```json
{
  "collection": "childhood",
  "bounds": {"min_select": 5, "max_select": 25},
  "moments": [
    {
      "id": "uuid",
      "title": "The monsoon cycle ride",
      "snippet": "first ~200 chars of the narrative…",
      "life_period": "childhood",
      "picked": true,
      "suggested_collection": "childhood",
      "used_in": ["festivals"]
    }
  ]
}
```

- **Ordering is meaningful:** picked moments first, in curation-rank order
  (best fit / most vivid first), then the rest of the qualifying pool.
  Render top-to-bottom as given.
- **`picked`** — pre-select these. For the 5 grid collections they come from
  one big-LLM curation pass; for **`wisdom`** every moment is `picked: true`
  (the chapter book lenses the whole pool; the user's job there is excluding).
- **`suggested_collection`** — the grid slug the curator assigned this moment
  to; `null` if unassigned (and always `null` on the wisdom preview). Render
  as a subtle hint chip on non-picked moments ("suggested for Festivals").
  Explicit user choice overrides it — never block an add because of it.
- **`used_in`** — collections of this person's **already-rendered (complete)**
  storybooks that used this moment. Render as a warning chip ("also appears
  in Festivals"). Informational only, never blocking.
- **`bounds`** — enforce client-side: disable confirm when the selected count
  is below `min_select` or above `max_select`. `min_select` is 5, relaxed to
  3 by the agent when the person's whole pool is under 5 — read it from the
  response, don't hardcode.

**Latency:** the **first** preview after the moment pool changes runs one
~15s LLM curation call — show a loading state ("choosing the best
memories…"). Every subsequent preview (any collection, until new moments are
extracted) hits a Valkey cache and returns instantly. Client timeout: 30s.

**Errors:** `400` unknown collection · `404` person not found · `409` too
few qualifying moments (same user-facing "keep sharing memories" detail as
create — reuse the existing empty state) · `502` curation LLM failure
(transient; offer retry).

## 3. `POST /storybooks` — the new field

```json
{
  "storybook_id": "uuid",
  "person_id": "uuid",
  "collection": "childhood",
  "pdf_put_url": "…", "cover_put_url": "…", "page_put_urls": ["…"],
  "anchor_photo_get_url": "…",
  "moment_ids": ["uuid", "uuid", "…"]
}
```

- ≤ 64 entries; the agent dedupes, so double-sends are harmless.
- Every id must belong to the preview's qualifying pool → otherwise **400**
  (`unknown or non-qualifying moment ids`).
- After dedupe the count must sit within the preview's `bounds` → otherwise
  **409** (`pick between X and Y moments (got N)`).
- Response is the existing shape; `moments_count` now equals the confirmed
  count (drives any "N memories" copy on the generating card).
- **Omit the field entirely** for the auto flow — do not send `[]` (an empty
  list is a below-min selection and 409s).

**Stale-preview race:** if extraction supersedes a moment between preview
and confirm, the create can 400. Handle by re-running the preview (fresh
pool, cached picks recomputed) and asking the user to re-confirm. Rare, but
don't dead-end on it.

## 4. What does NOT change

- Presigned-URL minting, content types, expiry, `storybook_id` generation —
  all exactly per `STORYBOOK_PYTHON_NODE_PROMPT.md` §3.
- `storybook_render_complete` LISTEN handler and URL-column writes.
- `POST /storybooks/{id}/regenerate` and `/edit` bodies. The agent now keeps
  a user-confirmed slice across these internally (and falls back to
  auto-curation only if supersession shrinks the slice below 3 moments).
- No new env vars, queues, migrations, or grants.

## 5. Acceptance checklist

- [ ] Collection tap runs `POST /storybooks/preview` before any URL minting;
      loading state covers the first-call ~15s curation.
- [ ] Checklist renders picked-first order, `picked` pre-selected,
      `suggested_collection` hint chips, `used_in` warning chips.
- [ ] Confirm disabled outside `bounds` (read from the response, not
      hardcoded); count copy matches the selection.
- [ ] Confirm sends the visible selection as `moment_ids` (including the
      no-edits "Looks good" case); Skip / flag-off omits the field.
- [ ] `400` on create (stale selection) re-opens the preview instead of
      dead-ending.
- [ ] `409` too-thin renders the existing "keep sharing memories" empty
      state; `502` offers retry.
- [ ] Regression: create WITHOUT `moment_ids` still renders end-to-end
      (auto-curate path untouched).
