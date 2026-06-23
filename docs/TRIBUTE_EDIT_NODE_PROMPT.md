# Node Prompt — Edit a tribute video

**For:** the Node Backend team.
**Status:** agent side built (endpoints + tests). One Node change outstanding:
wire an "Edit" action on a rendered tribute — tappable suggestion chips + a
free-text box that call the new endpoints. No migration required.

## Why

After a tribute video renders, the family wants to nudge it — "focus on his
workshop years", "gentler artwork", "warmer tone" — without redoing the whole
flow. The agent now exposes an **edit** endpoint that re-renders the tribute
guided by free-text adjustments, plus a **suggestions** endpoint that returns
subject-specific chips so the user isn't staring at a blank box.

Edit re-rolls the video guided by those instructions: **both the captions and
the artwork change**, shaped toward what the family asked for. It is a
*steering wheel, not a text editor* — it does not let you set an exact caption
verbatim; it tells the storyteller what to emphasize, downplay, or re-tone.

## The render boundary (recap)

Tribute video/PDF are rendered by the **agent** (`flashback.workers.
tribute_render`), not by Node's `artifact_generation` worker. Node's role:

1. Mint presigned URLs — a GET for the prime photo, PUTs for the MP4, the PDF,
   and (optionally) the cover poster JPEG.
2. Pass them into the edit request.
3. LISTEN on the `tribute_render_complete` Postgres NOTIFY and write
   `tributes.video_url` / `pdf_url` (and `thumbnail_url` when `poster_present`)
   from the keys it minted.

Edit changes none of this — it reuses the same render + completion path as
generate/regenerate. Node never writes `tributes.status` (agent-owned).

## Endpoint 1 — `POST /tributes/{tribute_id}/edit`

```jsonc
// request
{
  "person_id": "uuid",                            // must own the tribute
  "instructions": "Lean on the fishing trips.",   // the new edit (or a tapped chip's `instruction`); may be empty if prior_instructions is non-empty
  "prior_instructions": ["Make it warmer."],      // EVERY accepted edit so far, in order
  "video_put_url": "https://… (PUT, REQUIRED)",
  "pdf_put_url":   "https://… (PUT, REQUIRED)",
  "poster_put_url": "https://… (PUT, optional — cover thumbnail)",
  "prime_photo_get_url": "https://… (GET, optional — portrait source)"
}
```

```jsonc
// response (same shape as /generate and /regenerate)
{
  "job_id": "uuid",
  "tribute_id": "uuid",
  "artifact_kind": "tribute_video",
  "enqueued": true,
  "percent": 100,
  "ready": true,
  "scene_count": 14            // reused from the stored inputs
}
```

Behavior:

- Reuses the tribute's stored render inputs **verbatim** (memories, contributor
  message, look, render knobs) and applies `prior_instructions + [instructions]`
  as the family's edit requests. Later requests win on conflict.
- Overlays the fresh URLs + a new `composed_at`, flips `status` to
  `generating`, and re-enqueues the render. The bumped `composed_at` makes any
  in-flight older render go stale and skip, so a double-tap can't produce two
  videos.
- Completion fires the same `tribute_render_complete` NOTIFY as a first
  generate.

Errors:

| status | when |
|---|---|
| `400` | `video_put_url` or `pdf_put_url` missing |
| `400` | neither `instructions` nor `prior_instructions` has content (a no-op edit — call `/regenerate` instead) |
| `404` | tribute not found, `person_id` mismatch, **or never generated** ("call /generate first") |

### The instruction stream is cumulative — Node owns the history

Exactly like the moments `/edit` contract: the agent does **not** keep your
edit history. Node stores the list of accepted edits and sends the **full
`prior_instructions` array on every call**; the agent applies the whole list in
order each render. To "undo" an edit, resend the list without it. A tapped chip
is just its `instruction` text appended to that list.

## Endpoint 2 — `POST /tributes/{tribute_id}/edit-suggestions`

```jsonc
// request
{ "person_id": "uuid" }
// response
{
  "suggestions": [
    { "label": "More about his workshop", "instruction": "Lean the arc toward his woodworking and the workshop." },
    { "label": "Gentler artwork",         "instruction": "Soften the artwork — calmer light, gentler brushwork." }
    // ~4–5 chips
  ]
}
```

- A small LLM generates these from the tribute's stored memories, so they read
  as subject-specific. On any failure it returns a small generic catalog, so
  the list is **never empty**.
- `404` if the tribute was never generated.
- Cheap to call; it reflects edits already applied and avoids repeating them.
  Call it each time you open the edit UI.

## What Node must do

1. **Offer "Edit"** on a tribute whose video has rendered at least once (it has
   a `video_url`, or `status` was `complete`/`failed`).
2. **Populate the chip row** — call `/edit-suggestions`, render each `label` as
   a tappable chip, plus a free-text box.
3. **On submit:** the chosen chip's `instruction` (or the typed text) becomes
   `instructions`. **Mint fresh presigned URLs** (PUTs for MP4 + PDF, optional
   poster PUT, and a GET for the prime photo — the previous render's URLs have
   expired). Always re-mint `prime_photo_get_url` when a prime photo exists, or
   the cover falls back from the photo portrait to an illustrated opener.
4. **Call** `POST /tributes/{tribute_id}/edit` with `person_id`,
   `instructions`, the running `prior_instructions`, and the URLs.
5. **Append** the applied instruction to your stored history for next time.
6. **Completion is unchanged** — your existing `tribute_render_complete`
   listener writes the new `video_url` / `pdf_url` / `thumbnail_url` (overwrite
   the old keys, or write new ones if you minted new keys to keep versions).

## What Node does NOT need to change

- **No new content, no flow re-run.** You never re-send memories, the message,
  presets, or render knobs — the agent has them stored.
- **No edit-history persistence on the agent.** It is stateless on history; you
  own `prior_instructions`.
- **No completion-handler changes.** Same NOTIFY, same URL columns, same
  agent-owned `status` lifecycle.
- **Background music is automatic.** Edited videos carry the same gentle piano
  bed the agent muxes in at render time — no field, no asset to host.

## Acceptance check

1. On a rendered tribute, open Edit → `/edit-suggestions` returns a non-empty,
   subject-specific chip list.
2. Tap a chip (or type text). Node mints fresh PUT URLs (MP4 + PDF) + a fresh
   GET URL for the prime photo and calls `POST /tributes/{id}/edit`.
3. Response is `200` with `enqueued: true`; `tributes.status` is `generating`.
4. On completion the new MP4 visibly reflects the instruction (e.g. more
   workshop scenes / gentler art), with the captions and artwork re-rolled.
5. A second edit that carries the first instruction in `prior_instructions`
   keeps **both** adjustments.
6. `/edit` with empty `instructions` and empty `prior_instructions` → `400`.
7. `/edit` or `/edit-suggestions` on a never-generated tribute → `404`.
