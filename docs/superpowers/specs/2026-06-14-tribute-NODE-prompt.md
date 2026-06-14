# Node Integration Brief — Tribute Output (video + storybook)

**Audience:** the Node.js Backend team (separate repo). **Author:** Python
agent service. **Date:** 2026-06-14.

This is a build brief for the Node side of the **Tribute** feature: a
contributor-voiced, shareable **video** (hero) + **storybook** about the
subject of a legacy, launched via a **Father's Day** campaign skin but
built as a year-round capability. The Python agent service side is built,
tested, and committed. **Nothing renders until Node builds its half** —
that's the critical path described here.

---

## 0. The boundary (unchanged)

Per the existing contract:
- **Agent owns** all writes to the canonical Postgres graph, Working
  Memory, and pushing jobs onto the `artifact_generation` SQS queue. It
  composes the full generation context and writes it to Postgres **before**
  pushing a trigger-only SQS message.
- **Node owns** auth, sessions/transcripts (DynamoDB), all user-facing
  Postgres **reads** for the UI, and **consuming `artifact_generation`** —
  calling the image/video model, uploading to S3, and writing the URL
  columns back to Postgres.

The tribute feature adds **two new `artifact_kind`s** and a **compiled
multi-scene job shape** to that queue. Everything else reuses existing
patterns.

---

## 1. THE BIG LIFT — compiled tribute renderer

Today your `artifact_generation` worker handles **single-record** artifacts
(one moment → one video, one entity → one image). Tribute jobs are
**compiled**: one job → an ordered sequence of painterly scenes stitched
into a video, or a multi-page storybook. You need a new code path for these.

### 1.1 The SQS trigger (same envelope as today, two new `artifact_kind`s)

```json
{
  "job_id": "<uuid>",
  "record_type": "tribute",
  "record_id": "<tributes.id>",
  "person_id": "<persons.id>",
  "artifact_kind": "tribute_video",   // OR "storybook"
  "source": "auto",
  "composed_at": "2026-06-14T...Z",
  "enqueued_at": "2026-06-14T...Z"
}
```

The message is a **trigger only** — no prompt content. Read the context
from Postgres (next section).

### 1.2 Reading the context — NOTE: keyed by artifact_kind

For moments/entities/threads/persons, `latest_generation_context` is a
**single** context object. For **`tributes` it is keyed by artifact_kind**,
because one tribute row carries up to two artifacts:

```json
// tributes.latest_generation_context
{
  "tribute_video": { ...video context... },
  "storybook":     { ...storybook context... }
}
```

Your worker must read `latest_generation_context -> <artifact_kind>` (the
key matching the job's `artifact_kind`), **not** the whole column.

**Stale-skip:** each sub-context has its own `composed_at`. Skip the job if
`latest_generation_context[artifact_kind].composed_at != message.composed_at`
(a newer regenerate superseded it). This is per-artifact-kind, so a
storybook regenerate never invalidates an in-flight video job.

### 1.3 `tribute_video` context shape

```json
{
  "scenes": [
    {
      "moment_id": "<uuid>",
      "prompt": "<full painterly scene prompt, preset modifier already applied>",
      "negative": "<negative prompt — photoreal/deepfake bans already included>",
      "caption": "Short on-screen caption, 4-10 words",
      "duration_seconds": 8
    }
    // ... 3 to ~6 scenes, in render order
  ],
  "opening_caption": "Optional opening line (may be empty)",
  "message_text": "The contributor's polished message — the emotional climax",
  "closing_caption": "Optional closing line (may be empty)",
  "style_preset": "painterly_cinematic",
  "target_duration_seconds": 45,
  "negative_prompt": "<same negative, video-level>",
  "composed_at": "2026-06-14T...Z"
}
```

Render order: `opening_caption` → each scene (image generated from
`prompt`+`negative`, held for `duration_seconds`, with `caption` overlaid)
→ `message_text` as the climax beat → `closing_caption`. Aim for
`target_duration_seconds` total (the per-scene durations already sum to ~it).

- **`prompt` already includes the painterly style** (the preset modifier is
  baked in by the agent). Render it as-is; do not re-add style.
- **`negative` already bans photorealism + deepfake likeness** — keep it.
- `message_text` is text-on-screen (v1 has **no voice cloning / TTS of the
  subject**). An optional contributor voiceover is your call, out of agent scope.

### 1.4 `storybook` context shape

```json
{
  "cover": { "caption": "...", "style_preset": "storybook" },
  "pages": [
    { "moment_id": "<uuid>", "prompt": "...", "negative": "...", "caption": "..." }
    // up to (max_pages - 1) content pages
  ],
  "message_page": { "text": "The contributor's polished message" },
  "closing_caption": "...",
  "style_preset": "storybook",
  "max_pages": 9,
  "negative_prompt": "...",
  "composed_at": "2026-06-14T...Z"
}
```

Layout: cover → content pages (each an image from `prompt`+`negative` with
`caption`) → a final message page rendering `message_page.text`. **Hard cap
9 pages total** (cover + ≤8 content). Same style/negative rules as the video.

### 1.5 Writing results back

Write to the **`tributes`** row (you already write URL columns on the other
artifact tables):
- `tribute_video` → `video_url` (+ `thumbnail_url` if you produce one).
- `storybook` → `image_url` (cover) + `thumbnail_url`.

**Do NOT write `tributes.status`** — it's agent-owned lifecycle bookkeeping
(`draft/ready/generating/complete`). The agent sets it to `generating` when
it enqueues. For the UI, treat **URL presence** as "done" (mirrors how the
existing per-record artifacts work). If you want an explicit `complete`
flip, request an agent callback endpoint — don't write the column directly.

---

## 2. The agent ↔ Node API surface (what Node calls / reads)

All agent endpoints are unauthed at the agent (Node is the auth boundary),
service-token + private network as today.

### 2.1 Reads (straight from Postgres, like `active_themes_with_tier`)

**`tribute_status` view** — the completion meter + lock-card data:

| column | meaning |
|---|---|
| `id`, `person_id`, `theme_id`, `status` | the tribute row |
| `memories_count` | qualifying moments for the person |
| `message_present` | contributor message captured yet? |
| `appearance_present` | ground-truth appearance fields present? |
| `signature_present` | a trait or saying/mannerism present? |
| `percent` | 0–100 weighted completion (memories 40 / message 30 / appearance 20 / signature 10) |
| `ready` | all four slots satisfied → video can be generated |
| `video_url`, `image_url`, `thumbnail_url` | what you wrote back |

### 2.2 `GET /tribute-campaigns` — which campaign to feature

```json
{
  "campaigns": [
    {"slug": "default", "display_name": "A Tribute", "featured": false, "is_active": false, "active_start": null, "active_end": null},
    {"slug": "fathers_day_2026", "display_name": "A Letter to Dad", "featured": true, "is_active": true, "active_start": "2026-06-01", "active_end": "2026-06-22"}
  ],
  "active_featured_slug": "fathers_day_2026"
}
```

During the active window, **feature the tribute flow first in the UX**
(your placement call). Pass the `active_featured_slug` as `campaign` into
the flow (below) so the agent uses Father's Day copy + video length.

### 2.3 Session start carries the tribute context

`POST /session/start` — `session_metadata` accepts:
- `theme_id` (the tribute theme — see the **gap in §4**),
- `archetype_answers` (the expanded MC answers, same shape as theme unlock),
- **`campaign`** (NEW — e.g. `"fathers_day_2026"`; drives skin copy + video length).

### 2.4 The message capture sidecar on `/turn`

When the agent surfaces a **message tap** (a `Tap` with `kind:"message"`,
`question_id:null` in `metadata.taps`), render it as a card (free-text +
skip). The user's answer comes back on the **next `/turn`** as a new
sidecar field (mirrors `ground_truth_answer`):

```json
{ "message_answer": { "free_text": "...", "option_label": null, "skipped": false } }
```

The agent polishes it and stores it; it **never enters the transcript**.
Like other `question_id:null` taps, **do not** post a `question_decision`
for it.

### 2.5 Live meter on `/turn`

Every `/turn` response `metadata` (and the SSE `meta`/`done` events) now
includes, when the session is in a tribute flow:

```json
"tribute_progress": {
  "percent": 60,
  "ready": false,
  "slots": [
    {"key": "memories", "label": "Shared memories", "filled": true},
    {"key": "message", "label": "Your message", "filled": false},
    {"key": "appearance", "label": "How they looked", "filled": true},
    {"key": "signature", "label": "What made them them", "filled": true}
  ]
}
```

Use it to drive a live "X% — what's left" meter. It's monotonic within a
tribute. Graph-backed slots only flip after extraction commits — refresh on
the existing `extraction_complete` NOTIFY (or just re-read `tribute_status`).

### 2.6 `POST /tributes/{tribute_id}/generate`

```json
// request
{ "person_id": "<uuid>", "artifact_kind": "tribute_video", "preset": null, "campaign": "fathers_day_2026" }
// response
{ "job_id": "...", "tribute_id": "...", "artifact_kind": "tribute_video",
  "enqueued": true, "percent": 100, "ready": true, "scene_count": 5 }
```

Gating (agent-enforced): **video** requires `ready=true` (409 otherwise);
**storybook** requires ≥3 qualifying moments. Call once per artifact kind.

---

## 3. End-to-end UX sequence Node orchestrates

1. `GET /tribute-campaigns` → if a campaign `is_active`, feature it.
2. **Enter the tribute** (see §4 gap) → obtain the tribute `theme_id`.
3. `POST /themes/{theme_id}/unlock_prepare` → returns 6–8 archetype MC
   questions (more than the usual 3–4). Render them with chips + free-text;
   optionally persist partial answers via `archetype_progress`.
4. `POST /session/start` with `session_metadata.theme_id` +
   `archetype_answers` + `campaign`.
5. Run the conversation via `/turn` (or `/turn/stream`):
   - render the live meter from `metadata.tribute_progress`;
   - when a `kind:"message"` tap appears, show the message card; post the
     answer back as `message_answer` on the next `/turn`.
6. When `tribute_status.ready` (meter at 100%), enable **"Make my video"** →
   `POST /tributes/{id}/generate` (`tribute_video`), and optionally
   `storybook`.
7. Your worker renders the compiled job(s), uploads to S3, writes the URL
   columns. UI polls `tribute_status` for URL presence and shows the
   shareable result.

---

## 4. OPEN ITEMS / gaps to resolve with the agent team

1. **⚠️ Entry endpoint is missing (agent side).** The tribute theme is
   seeded **on demand**, not at person creation, and there is currently **no
   route that seeds it** — so there is no way yet to obtain the tribute
   `theme_id` to start step 2–3. The agent team will add an entry endpoint
   (proposed: `POST /tributes/start {person_id, campaign?}` → ensures the
   tribute theme + an open tribute row, returns `{theme_id, tribute_id}` and
   the unlock questions). **Node's flow depends on this; treat it as a
   prerequisite.**
2. **Status `complete` flip.** Decide whether Node infers completeness from
   URL presence (preferred, zero new contract) or the agent exposes a
   callback to flip `tributes.status='complete'`. Default: URL presence.
3. **Compiled renderer model choice + duration handling** is entirely
   Node-side. The agent only guarantees the context shape above.
4. **Storybook for non-tribute legacies** (a general "book of memories" any
   user can generate) is designed but **not built** on the agent side yet —
   out of scope for this launch; the tribute storybook is tribute-flow-only.

---

## 5. Quick reference — what's already done on the agent side

- Migration 0027: `tributes` table, `tribute` theme kind, `tribute_status` view.
- Capture: expanded archetype questions, the message-invitation tap + the
  `message_answer` sidecar (polished, never extracted), WM plumbing.
- Assembly: big-LLM scene ordering + captions + message placement
  (chronological fallback), compiled video + storybook context builders
  (painterly preset + photoreal/deepfake negatives, 9-page / 45s caps).
- `POST /tributes/{id}/generate`, `GET /tribute-campaigns`, the `/turn`
  live meter, the Father's Day skin (copy + featured window + video length).
- 38 tests passing against the test DB; zero regressions vs `main`.

The single hard dependency for a working end-to-end demo is **§1 (your
compiled renderer)** plus **§4.1 (the agent entry endpoint)**.
