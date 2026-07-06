# Node Prompt — Storybook per-collection eligibility (tag-gated chooser)

**For:** the Node Backend + frontend team.
**Status:** agent side built (design
`docs/superpowers/specs/2026-07-06-storybook-collection-eligibility-design.md`;
`API.md` §7c, `NODE_INTEGRATION.md` §7c, `CLAUDE.md` §3, `SCHEMA.md` §2.2,
migration 0036 updated).
**No new queue, no NOTIFY changes.** One migration (agent-owned), one optional
query param, and a chooser-UI change. **All Node work is additive and
backward-compatible** — if you ship nothing, the existing flow keeps working;
you just won't show the new "not ready yet" affordance.

---

## TL;DR

A storybook could previously be minted for a collection that **none of the
person's moments actually fit** — the render then hallucinated an "Adventures"
book out of family-dinner memories. The agent now **tags each moment with the
collections it genuinely fits** and **refuses to start a render** for a
collection that lacks enough fitting material.

Your side is two small things:

1. **`GET /storybook-collections?person_id=<uuid>`** now returns
   `tagged_count` + `eligible` per collection. Use it to **lock collection
   cards** the person can't make yet and show a "3/5 stories" progress badge —
   exactly like the theme lock cards.
2. **Handle a per-collection `409`** on preview/create (it now fires when a
   *specific* collection is too thin, not just when the whole legacy is empty).

The preview flow, presigned-URL handshake, LISTEN/NOTIFY completion, and
regenerate/edit calls are **unchanged on the wire**. One nice side effect:
**the preview is now instant** (the ~15s curation wait is gone).

---

## 1. `GET /storybook-collections` — the eligibility fields

**Bare call (unchanged):**

```
GET /storybook-collections
→ [{ "slug": "childhood", "display_name": "Childhood Memories",
     "layout": "grid", "page_count": 7 }, … 6 rows]
```

**Person-scoped call (new):**

```
GET /storybook-collections?person_id=<uuid>
→ [{ "slug": "childhood", "display_name": "Childhood Memories",
     "layout": "grid", "page_count": 7,
     "tagged_count": 5, "eligible": true },
   { "slug": "adventurous", …, "tagged_count": 2, "eligible": false },
   { "slug": "wisdom",      …, "tagged_count": 11, "eligible": true }, …]
```

- **`tagged_count`** — how many qualifying moments the collection can draw on.
  For the 5 grid collections it's the count tagged to that collection; for
  **`wisdom`** it's the whole qualifying pool (wisdom reads everything through
  a lens, so it's never tag-gated).
- **`eligible`** — `true` when `tagged_count ≥ floor`. **Floor is 5 for grid
  collections, 3 for `wisdom`.** Don't hardcode the floor for a progress
  badge — but if you want a "N / floor" label, the floor is
  `slug === "wisdom" ? 3 : 5`.
- Without `person_id`, both fields are **absent** (not `null` in the bare
  registry response — they simply aren't present). Treat missing as "unknown,
  don't gate."

**UI:** render an ineligible collection as a **locked card** with a
`{tagged_count}/{floor} stories` badge and muted styling — the same pattern as
the theme lock cards. Tapping a locked card should explain "keep sharing
{name}'s memories to unlock this book," not open the preview. Only enable the
create/preview flow when `eligible === true`.

This is the fix for your original ask: **the UI shouldn't let the user start a
book that can't be made.** The agent enforces it too (below), but gating the
chooser is the good UX.

## 2. Preview + create — a per-collection 409

The bodies are **unchanged**. What changed is *when* `409` fires and that the
preview is now instant.

- **`POST /storybooks/preview`** and **`POST /storybooks`** return **`409`**
  when the *requested collection* is below its floor — even if the legacy has
  plenty of moments overall (they just don't fit *this* collection). The
  user-facing detail reads "Not enough stories yet — keep sharing memories of
  {name} (need at least 5 qualifying moments for this collection, have 2)".
  Reuse your existing "keep sharing memories" empty state. If you gate the
  chooser per §1 this should be rare (belt-and-suspenders / race only).
- **No more `502`** from preview. The preview used to run a ~15s curation LLM
  call that could fail with `502`; that call is **retired**. The preview is now
  a fast DB read — drop any `502` handling and the "choosing the best
  memories…" long-loading state for preview (a normal spinner is plenty).

## 3. Preview response — one new field, one deprecation

```json
{
  "collection": "childhood",
  "bounds": {"min_select": 5, "max_select": 25},
  "moments": [
    {
      "id": "uuid",
      "title": "The monsoon cycle ride",
      "snippet": "first ~200 chars…",
      "life_period": "childhood",
      "picked": true,
      "collections": ["childhood", "adventurous"],
      "suggested_collection": "adventurous",
      "used_in": ["festivals"]
    }
  ]
}
```

- The moment pool is now **collection-scoped**: for a grid collection it's
  exactly the moments tagged to that collection; for `wisdom` it's the whole
  pool. So every moment in the list already fits — the user's job is trimming,
  not hunting.
- **`picked`** — pre-select as before. It's now deterministic (tagged pool,
  chronological, moments already used in a completed book pushed to the
  bottom), not an LLM guess.
- **`collections`** (new) — the moment's full tag list. Use it for a
  cross-book "also fits Adventures" chip if you want richer hinting.
- **`suggested_collection`** (**deprecated**) — now just the first tag other
  than the previewed collection (a single "also fits" hint). Kept so you don't
  have to change anything today; prefer `collections` when you touch this.
- **`used_in`**, **`bounds`** — unchanged (`min_select` is 5 for grid, 3 for
  `wisdom` on a thin pool; read it, don't hardcode).

## 4. What does NOT change

- `POST /storybooks` body, `moment_ids`, presigned-URL minting, content types,
  `storybook_id` generation — all per `STORYBOOK_PYTHON_NODE_PROMPT.md` §3 and
  `STORYBOOK_MOMENT_PICKER_NODE_PROMPT.md`.
- `storybook_render_complete` LISTEN handler and URL-column writes.
- `POST /storybooks/{id}/regenerate` and `/edit` bodies.
- No new env vars, queues, or NOTIFY channels. Migration 0036 is **agent-owned**
  (adds `moments.storybook_collections`) — you don't run it, and it doesn't
  touch any Node-read view; `active_storybooks` is unchanged.

## 5. Rollout note

Existing legacies get their moments tagged by a one-time agent-side backfill
at deploy, so `tagged_count` is accurate for current users immediately. Until
the backfill runs for a given legacy, grid collections may read `eligible:
false` — that's correct (unverified moments don't count), not a bug.

## 6. Acceptance checklist

- [ ] Chooser calls `GET /storybook-collections?person_id=…` and renders
      locked cards with a `{tagged_count}/{floor}` badge for `eligible: false`.
- [ ] Locked card tap shows "keep sharing memories" guidance, not the preview.
- [ ] Preview/create surface the per-collection `409` via the existing
      empty-state copy (rare if the chooser gates correctly).
- [ ] Preview loading state is a normal spinner (no ~15s curation wait, no
      `502` handling on preview).
- [ ] Bare `GET /storybook-collections` (no `person_id`) still works and the
      response has no `tagged_count`/`eligible` keys.
- [ ] Regression: create WITH and WITHOUT `moment_ids` both render end-to-end.
