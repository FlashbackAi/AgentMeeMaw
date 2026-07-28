# Node Prompt — Regenerate a tribute video

**For:** the Node Backend team.
**Status:** agent side built (endpoint + tests). One small Node change
outstanding: wire a "Regenerate" action that mints fresh presigned URLs and
calls the new endpoint. No migration required.

## Why

Users want a "Regenerate" button on a finished tribute video — re-roll the
render without re-doing the whole tribute flow. The agent now exposes
`POST /tributes/{tribute_id}/regenerate`, which re-renders **from the exact
same stored inputs** as the last generate (same memories, same contributor
message, same look).

**Both the text and the pictures change on every regenerate.** Same source
data, fresh creative roll: the agent re-runs the LLM that writes the cover
title, opener, per-page captions, and closing (so the wording differs), then
re-paints the character portrait and every page illustration through the image
model (so the artwork differs). A user can keep tapping regenerate until they
like the result. The new video also carries a soft piano backing track
(agent-side, nothing for Node to do — see the note at the end).

Because it reuses the stored assembly inputs, the **only** thing Node must
supply is a fresh set of presigned URLs — the URLs from the original generate
have long since expired by the time a user taps regenerate.

## The render boundary (unchanged, recap)

Tribute video/PDF are rendered by the **agent** (`flashback.workers.
tribute_render`), not by Node's `artifact_generation` worker. Node's role for
tributes is:

1. Mint presigned URLs: a GET for the prime photo, PUTs for the MP4, the PDF,
   and (optionally) the cover poster JPEG.
2. Pass them into the generate/regenerate request.
3. LISTEN on the `tribute_render_complete` Postgres NOTIFY and write
   `tributes.video_url` / `pdf_url` (and `thumbnail_url` when
   `poster_present`) from the keys it minted.

Regenerate changes **none** of this — only the trigger endpoint differs.

## The contract change — `POST /tributes/{tribute_id}/regenerate`

```jsonc
// request
{
  "person_id": "uuid",                 // owning legacy; must match the tribute
  "video_put_url": "https://… (PUT, REQUIRED)",
  "pdf_put_url":   "https://… (PUT, REQUIRED)",
  "poster_put_url": "https://… (PUT, optional — cover thumbnail)",
  "prime_photo_get_url": "https://… (GET, optional — portrait source)"
}
```

```jsonc
// response (same shape as /generate)
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

- Reuses the prior `tribute_video` render context **verbatim** — memories,
  contributor message, archetype leads, subject look (ground truth), de-age
  flag, page count, and all render knobs. Node sends **none** of those again.
- Overlays only the four URL fields above + a new `composed_at`, then
  re-enqueues the render. The bumped `composed_at` makes any still-in-flight
  older render go stale and skip, so a double-tap can't produce two videos.
- The agent flips `tributes.status` to `generating`; completion fires the same
  `tribute_render_complete` NOTIFY as a first generate.

Errors:

| status | when |
|---|---|
| `400` | `video_put_url` or `pdf_put_url` missing |
| `404` | tribute not found, `person_id` mismatch, **or never generated** ("nothing to regenerate; call /generate first") |

## What Node must do

1. **Show "Regenerate"** on a tribute whose video has rendered at least once
   (i.e. it has a `video_url`, or `status` was `complete`/`failed`).
2. **Mint fresh presigned URLs** exactly as for the first generate — a PUT for
   the MP4, a PUT for the PDF, optionally a PUT for the poster and a GET for the
   prime photo. Expiry must cover queue latency + render time (same as
   generate). You may reuse the same S3 keys (overwrite the old video) or new
   keys (keep versions) — your call.
3. **Call** `POST /tributes/{tribute_id}/regenerate` with `person_id` + those
   URLs.
4. **Handle completion unchanged** — your existing `tribute_render_complete`
   listener writes `video_url` / `pdf_url` / `thumbnail_url`. For regenerate it
   overwrites the prior values (or writes new keys if you minted new ones).
5. **(Important) Re-mint `prime_photo_get_url` if you want the portrait cover.**
   The old GET URL has expired. If you omit it, the agent clears it and the
   opener falls back to an illustrated cover rather than the photo portrait —
   so always pass a fresh GET URL when a prime photo exists.

## What Node does NOT need to change

- **No new content, no flow re-run.** You do not re-send memories, the message,
  the campaign, presets, or any render knobs — the agent has them stored.
- **No content gating to enforce.** The agent gates on the prior render
  existing (404 otherwise); there is no `ready`/`percent` precondition for Node
  to check before regenerating.
- **No completion-handler changes.** Same NOTIFY, same URL columns, same
  `status` lifecycle (agent-owned — Node never writes `tributes.status`).
- **Background music is automatic.** Regenerated (and freshly generated)
  tribute videos now include a gentle piano track muxed in by the agent at
  render time. Nothing to wire — the MP4 you receive simply has an audio
  stream. No new field, no separate audio asset to host.

## Acceptance check

1. Take a tribute with a finished video. Tap "Regenerate".
2. Node mints fresh PUT URLs (MP4 + PDF) + a fresh GET URL for the prime photo,
   and calls `POST /tributes/{id}/regenerate`.
3. Response is `200` with `enqueued: true`; `tributes.status` is `generating`.
4. On completion the `tribute_render_complete` NOTIFY fires and Node writes the
   new `video_url` / `pdf_url` / `thumbnail_url`. The new MP4 tells the same
   memories with **freshly written captions and freshly painted artwork**,
   background music, and the portrait cover.
5. Calling regenerate on a tribute that was **never** generated returns `404`.
