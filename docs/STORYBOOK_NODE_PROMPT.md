# Node Prompt — On-demand storybooks (multi, tagged, regenerate/edit)

**For:** the Node Backend team.
**Status:** agent side built on `main` (not yet committed at time of writing).
Requires agent migration **0032** applied to Postgres before the new `tags`
column/view field and endpoints work.

## Why

Storybooks used to be **auto-generated** at session wrap, gated on "8+ new
qualifying moments since the last one," and capped at the **12 newest-extracted**
moments — which silently dropped older memories and gave the user no control.

That is replaced. Now:

- A legacy can hold **many** storybooks, each minted **on demand** (the user
  presses a button — Node calls the agent).
- Each request can optionally **scope** the book (one theme, or one life period);
  no scope = the whole qualifying pool, ordered life-chronologically.
- Each book carries 1–3 **emotional tags** (e.g. `warmth`, `grief`) that the
  agent's LLM picks and tones the prose to. Node maps the **stable slugs** to a
  render template.
- Books can be **regenerated** (re-render with a new style/preset/tags) and
  **edited** (reshape the text + scenes via free-text instructions), mirroring
  the moment-artifact regenerate/edit flow.

**The big behavior change:** storybooks **no longer appear by themselves.** If
Node had any "a new storybook may show up after a session" expectation (polling,
a wrap-time refresh, etc.), that path is gone — nothing is produced unless Node
calls `POST /storybooks`.

## New endpoints

All three are trigger-only: the agent composes the full generation context,
writes it to the `storybooks` row, and pushes an `artifact_generation` job. Node
renders from Postgres exactly as it does for the existing storybook/tribute
artifacts (see "Artifact rendering" below).

### `POST /storybooks` — mint a new book

```jsonc
// request
{
  "person_id": "uuid",
  "scope": {                       // optional; omit for the whole pool
    "theme_id": "uuid | null",     // restrict to moments tagged to this theme
    "life_period": "string | null" // exact match on the moment's life period
  },
  "preset": "string | null"        // artifact style preset slug (see /artifact-presets)
}
```

```jsonc
// response 200
{
  "job_id": "uuid",
  "storybook_id": "uuid",
  "person_id": "uuid",
  "status": "generating",
  "source": "manual",
  "tags": ["warmth", "nostalgia"], // 0–3 registry slugs the agent chose
  "moments_count": 12,             // qualifying moments in the (scoped) pool
  "scene_count": 9,                // pages the assembler produced
  "enqueued": true
}
```

- **422** if the (scoped) pool has fewer than **3** qualifying moments — surface
  "not enough memories yet for this book."
- **404** if the person doesn't exist; **400** for an unknown `preset` slug.

### `POST /storybooks/{storybook_id}/regenerate` — re-render, text kept

```jsonc
// request
{
  "person_id": "uuid",
  "preset": "string | null",       // new style
  "tags": ["string"] | null        // optional override (≤3 slugs) for templating
}
```

Re-composes the page images with the new preset (captions/ordering kept) and,
if `tags` is supplied, overrides the stored tags. **No LLM call.** Same response
shape as above with `source: "regenerate"`. **404** if the book doesn't exist /
isn't owned by this person.

### `POST /storybooks/{storybook_id}/edit` — reshape text + scenes

```jsonc
// request
{
  "person_id": "uuid",
  "instructions": "string",            // newest edit, e.g. "make it warmer"
  "prior_instructions": ["string"],    // cumulative history (Node owns it; see below)
  "preset": "string | null",
  "tags": ["string"] | null            // optional: pin the register/tone
}
```

Re-runs the assembler over the **same** moment set with the cumulative edit
notes — it can drop / reorder / re-tone scenes, but does **not** pull in new
moments (that's a fresh `POST /storybooks`). Same response shape with
`source: "edit"`. **422** if the book's moments are all gone, **404** if not
found, **400** for a bad preset.

**Edit history is Node's, exactly like the moment artifact edit flow:** persist
each accepted instruction in your per-record Dynamo table and send the full
`prior_instructions` list (oldest→newest) on every edit call so the composed
prose carries every accepted edit in order. The agent stays stateless here.

## The `tags` registry → templates

`active_storybooks` now exposes a `tags TEXT[]` column (migration 0032). The
slugs are a fixed, stable contract — map them to render templates on the Node
side. Current registry (slug — label):

`warmth` — Warmth · `happiness` — Happiness · `nostalgia` — Nostalgia ·
`love` — Love · `pride` — Pride · `gratitude` — Gratitude ·
`resilience` — Resilience · `adventure` — Adventure · `mischief` — Mischief ·
`wonder` — Wonder · `longing` — Longing · `grief` — Grief · `peace` — Peace

- A book carries **0–3** tags, most-dominant first. `tags[0]` is the natural
  pick for a single-template mapping.
- Empty `tags` is valid (e.g. the LLM fell back) — default to a neutral template.
- Unknown slugs never reach you: the agent validates against this registry and
  drops anything off-list before storing.

If you need new slugs, ask the agent team to add them to the registry — don't
invent slugs Node-side, and never rename existing ones.

## Artifact rendering — what changed, what didn't

Mechanism is unchanged: read `storybooks.latest_generation_context`, render the
PDF, write `image_url`/`thumbnail_url`, flip `status` `generating → complete`
(or `failed`). Two things to note:

1. **No cover page.** A standalone storybook's context has **no `cover` key**
   (the tribute storybook still does). Open the book on the first content page;
   the closing line is the final card (`message_page.text`). The rest of the
   shape is the same as the tribute storybook context: `pages[]` (each with
   `prompt`, `negative`, `caption`, optional `accent`/`pull_quote`/`layout`),
   `message_page.text`, `closing_caption`, `style_preset`, `max_pages`,
   `negative_prompt`, `composed_at`.
2. **`source` values.** The `artifact_generation` job for
   `record_type="storybook"` now carries `source` ∈ `manual | regenerate | edit`
   (it used to always be `auto`). Use it for telemetry if you like; rendering is
   identical regardless. Still honor the `composed_at` stale-check (skip a job
   whose `composed_at` is older than the row's current
   `latest_generation_context.composed_at`).

## What Node must do

1. **Add the "create storybook" UX** and call `POST /storybooks`. Surface the
   optional scope (a theme picker and/or a life-period picker) and the preset
   picker (reuse `/artifact-presets`).
2. **Show the gallery from `active_storybooks`** (many per person) and render
   each card using the new `tags` for template selection.
3. **Wire regenerate + edit** buttons to the two endpoints; keep the edit
   `prior_instructions` history in Dynamo.
4. **Drop any reliance on auto-generated storybooks** — there are none now.
5. **Render with no cover page** for storybooks (point 1 above).

## What Node does NOT change

- **No new queue, no new render pipeline.** Same `artifact_generation` queue,
  same `latest_generation_context` read, same URL write-back.
- **No canonical-graph writes.** `tags` is agent-written; Node only **reads** it
  from `active_storybooks`.
- **Gender / painterly prompt wording** is already baked into the prompt text by
  the agent (see the contributor-gender prompt) — nothing to do here.

## Acceptance check

1. Create a legacy with ≥3 qualifying moments; `POST /storybooks` with no scope
   → a row appears with `status: "generating"`, a `tags` array, and a
   cover-less context; the PDF renders and `status` flips to `complete`.
2. `POST /storybooks` again → a **second** book appears (nothing superseded).
3. `POST /storybooks` with `scope.life_period` set → `moments_count` reflects
   only that period.
4. `POST /storybooks` on a legacy with <3 qualifying moments → **422**.
5. Regenerate one book with a different preset + `tags: ["grief"]` → same
   `storybook_id`, the card's template switches, the PDF re-renders.
6. Edit a book with "make it warmer" → the captions change on re-render; sending
   the same instruction again with it in `prior_instructions` stacks correctly.
