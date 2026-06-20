# Node Prompt — Tribute video render moved to the agent (presigned URLs)

**For:** the Node Backend team.
**Status:** agent side built (Python repo, branch TBD; specs +
`NODE_INTEGRATION.md` §7b, `API.md` §7b, `CLAUDE.md` §3 updated). Node work
outstanding before tribute videos render.

---

## TL;DR

The tribute output moved **off Node's renderer**. A tribute now produces a
**video** (shown in-app) + a **PDF** (print), rendered by the **agent's**
`tribute_render` worker. The tribute **storybook** artifact is **retired**.

Node's new job is small and boundary-preserving: **mint presigned S3 URLs**,
pass them on `/generate`, **listen** for completion, and **write the URL
columns**. The agent holds **no S3 credentials** and never writes URL columns —
your S3 + URL ownership is intact.

---

## 1. What Node STOPS doing

- **Stop consuming `artifact_generation` messages with `record_type='tribute'`.**
  The agent no longer pushes them. (Keep consuming `moment` / `entity` /
  `thread` / `person` — unchanged.)
- **Retire the tribute storybook renderer** (the green-punch-through template
  compositor) **for tributes**. If the standalone `/storybooks` keepsake-book
  feature still uses that renderer, keep it for that path — only *tributes* move.
- `POST /tributes/{id}/generate` with `artifact_kind='storybook'` now returns
  **410**. Stop sending it; send `artifact_kind='tribute_video'`.

## 2. The handshake (what Node DOES)

```
meter == 100%  →  Node mints presigned URLs  →  POST /tributes/{id}/generate
                       (video_put_url, pdf_put_url, prime_photo_get_url?)
   agent assembles + enqueues  →  tribute_render worker:
        GET prime photo → render MP4 + PDF → PUT to your URLs
        → status='complete' → NOTIFY 'tribute_render_complete'
   Node (LISTENing) → write tributes.video_url + pdf_url → show video
```

### 2a. Mint presigned URLs

Before calling `/generate`, mint three presigned URLs against your S3 bucket and
**remember the object keys** (you'll build the public URLs from them later):

| URL | Method | Object | Content-Type |
|---|---|---|---|
| `video_put_url` | **PUT** | your key (see *Key scheme* below) | `video/mp4` |
| `pdf_put_url` | **PUT** | your key (see *Key scheme* below) | `application/pdf` |
| `poster_put_url` | **PUT** | your key (see *Key scheme* below) | `image/jpeg` |
| `prime_photo_get_url` | **GET** | the contributor's prime/profile photo | — |

- **Expiry ≥ 24h.** The job sits in SQS and the render takes minutes; a 1h
  presign can expire before the worker runs.
- Sign the PUTs **for those exact content-types**, or sign **without enforcing
  content-type** — the worker sends `Content-Type` headers matching the table.
  (If your signed policy pins a different content-type, S3 returns 403 and the
  upload fails.)
- `prime_photo_get_url` is **optional but recommended**: when present, the
  opener page becomes a painterly **portrait of the subject** (image-to-image,
  likeness kept). Same photo source as the FD cover's `prime_photo_s3_key`
  (prime-years upload, else profile/legacy photo). Omit only when there's no
  photo at all (opener falls back to an establishing scene).
- `poster_put_url` is **optional but recommended**: when present, the worker
  PUTs the **cover poster** (the opener page — portrait + title, the video's
  first frame) as a JPEG, and the completion NOTIFY carries
  `poster_present:true`. Write `tributes.thumbnail_url` from this key (§2d) so
  the tribute card/thumbnail shows the cover instead of a stray mid-video
  frame. Omit to leave `thumbnail_url` untouched.

**Key scheme — your call (the agent is agnostic).** The agent only PUTs to the
URLs you sign and fires the NOTIFY with `tribute_id`/`person_id` (no keys), so
the listener must recover the public URL on its own. **Recommended:** derivable
**userId-scoped** keys (e.g. `sessions/<userId>/tributes/<tributeId>/video.mp4`
+ `…/storybook.pdf` + `…/poster.jpg`) — stable, zero storage, re-derive on NOTIFY via
`resolveUserId(personId)`, and keeps your `sessions/<userId>/` access-control
prefix. Re-render **overwrites** the same object (correct — a tribute is one
deliverable, not versioned); if you serve `video_url` via a CDN, **cache-bust
the written URL with the view's `rendered_at`**: `…/video.mp4?v=<rendered_at>`.
Persist job-scoped keys (Dynamo/column at `/generate`) only if you need
per-render URL history — you don't for a single deliverable.

### 2b. Call `/generate`

```jsonc
POST /tributes/{tribute_id}/generate
{
  "person_id": "<uuid>",
  "artifact_kind": "tribute_video",      // the only supported kind now
  "video_put_url": "https://<bucket>.s3...&X-Amz-Signature=...",  // REQUIRED
  "pdf_put_url":   "https://<bucket>.s3...&X-Amz-Signature=...",  // REQUIRED
  "poster_put_url": "https://<bucket>.s3...&X-Amz-Signature=...", // optional (cover poster)
  "prime_photo_get_url": "https://<bucket>.s3...&X-Amz-Signature=...", // optional
  "campaign": "fathers_day_2026",        // optional skin
  "cover_photo_is_prime_years": false    // false = de-age an older/current photo
}
```

Response `200` → `{ job_id, tribute_id, artifact_kind, enqueued, percent, ready,
scene_count }`. The agent has assembled the story, stored it + your URLs on the
row, flipped `status='generating'`, and enqueued the render.

**Errors to handle:**
- `409` — meter below 100% (`detail` has the percent). **Gate the button at
  100%** so this is rare.
- `400` — missing `video_put_url` / `pdf_put_url`.
- `410` — you sent `artifact_kind='storybook'` (retired).
- `404` — tribute not found / not owned by `person_id`.

**Not retry-safe.** A second `/generate` re-renders + re-enqueues (new cost). De-
dupe on your side (disable the button while `status='generating'`).

### 2c. Listen for completion — don't poll

The worker fires a **transactional** Postgres `NOTIFY` on channel
**`tribute_render_complete`** (exactly like `extraction_complete`, see
`NODE_INTEGRATION.md` §8.3):

```json
{ "event": "tribute_render_complete", "tribute_id": "…", "person_id": "…",
  "status": "complete", "video_present": true, "pdf_present": true,
  "poster_present": true }
```

Wire it like your existing `extraction_complete` listener:
- Hold a dedicated **session-pinned** `LISTEN tribute_render_complete` connection
  (a transaction-mode pooler silently drops `LISTEN`).
- Treat the NOTIFY as a **wake-up**; read the truth from the **`tribute_status`**
  view (`WHERE id = <tribute_id>`).
- **Durability backstop:** on (re)connect, re-query rows whose
  `status='complete'`/`'failed'` with a `rendered_at` newer than your watermark,
  to catch notifications missed while disconnected.

### 2d. Write the URL columns

On a `complete` signal, **Node writes** `tributes.video_url` and
`tributes.pdf_url` from the keys you minted in 2a (you chose them, so you know
the public URLs). The agent never writes these. **When you minted a
`poster_put_url` and the NOTIFY has `poster_present:true`, also write
`tributes.thumbnail_url`** from the poster key — that's the cover (the opener
page), so the tribute card shows the cover instead of a stray mid-video frame.
(If you prefer, you can still derive a thumbnail from the video yourself; the
poster just gives you the exact cover frame for free.)

`video_url`, `pdf_url`, and `thumbnail_url` are the **only** columns Node writes
on `tributes` — see `NODE_INTEGRATION.md` §6.5.

## 3. The `tribute_status` view (read surface)

Migration `0033` (applied by the agent's migrate step) adds to the view:
`pdf_url`, `rendered_at`, and `status` may now be `'failed'`. Full status
lifecycle: `draft → ready → generating → complete | failed | superseded`.

| You need… | Read |
|---|---|
| Is the meter full? | `percent` (gate the button at `100`; `ready` is also true) |
| Render done? | `status = 'complete'` + `video_url`/`pdf_url` non-null |
| Render failed? | `status = 'failed'` (offer "try again") |
| Print link | `pdf_url` |

**Failure handling.** A render that exhausts SQS retries lands in the DLQ and
emits **no** NOTIFY (mirrors extraction). If no completion arrives within your
timeout, show "still processing / try again" and re-query `tribute_status`.

## 4. UI

- **Unlock the "Create video" CTA at 100%** (was "storybook at 50%").
- While `status='generating'`: progress/spinner state.
- On complete: play `video_url` inline; **"Print" / "Download PDF" → `pdf_url`**.
- On `failed`/timeout: retry affordance (calls `/generate` again).
- There is **no separate storybook** deliverable anymore — one output (video),
  PDF is the print form of it.

## 5. Integration checklist

- [ ] Remove tribute branch from the `artifact_generation` consumer
      (`record_type='tribute'`). Keep `moment/entity/thread/person`.
- [ ] Retire the tribute storybook renderer path (keep standalone `/storybooks`
      if that feature stays).
- [ ] Presigned-URL minting helper (GET photo, PUT video, PUT pdf, PUT poster;
      ≥24h; content-types per §2a). Persist/derive the object keys.
- [ ] Update the `/generate` client: send `artifact_kind='tribute_video'` +
      the URLs (incl. `poster_put_url`); gate at `percent==100`; handle 400/409/410.
- [ ] `LISTEN tribute_render_complete` (clone the `extraction_complete`
      listener); on signal, write `video_url` + `pdf_url` (+ `thumbnail_url`
      from the poster key when `poster_present`), refresh UI.
- [ ] Tolerate new `tributes` columns (`pdf_url`, `rendered_at`) + `status`
      value `'failed'`.
- [ ] UI: 100% unlock, generating/complete/failed states, video player,
      Print→PDF.
- [ ] Monitoring: `tribute_render` DLQ depth; surface in your dashboards.

## 6. References (agent repo)
- `NODE_INTEGRATION.md` §7b (handshake), §6.5 (writable columns), §8.3 (NOTIFY pattern)
- `API.md` §7b (`POST /tributes/{id}/generate` request/response/errors)
- `CLAUDE.md` §3 (boundary: agent renders, no S3 creds, no URL-column writes)
- Spec: `docs/superpowers/specs/2026-06-20-tribute-video-pipeline-design.md`
