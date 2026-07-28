# Tribute Video Pipeline — Python-owned render (Father's Day flow)

**Date:** 2026-06-20
**Status:** design — awaiting review
**Supersedes the render half of:** `2026-06-14-tribute-output-design.md` (Node-rendered tribute storybook/video)

---

## 1. Goal

Replace the Node-rendered tribute storybook with a **Python-owned video** (primary,
shown in-app) plus a **PDF** (print on request), generated from the Father's Day
tribute theme flow. The agent assembles the story from FD-flow content; a new
Python worker renders watercolour pages (Gemini illustrations → templated
composite) into an MP4 + PDF and hands them to Node via presigned URLs. The
tribute unlocks at **100%** meter. The standalone tribute *storybook* artifact and
Node's tribute renderer are retired.

The look + motion are already proven in the spike at
[`scripts/storybook_prototype/`](../../../scripts/storybook_prototype/) (cream
watercolour pages, 8–10-word lines, opener portrait, layered-reveal + ink-bleed
video). This spec graduates that into the service.

## 2. Locked decisions (from review)

1. **Storage = presigned URLs; Python never touches S3 directly.** Node mints a
   presigned **GET** for the prime photo and presigned **PUT**s for the video +
   PDF, and passes them on `/generate`. The worker downloads/uploads through
   those. Node writes the URL columns after completion. Python holds **no S3
   creds** and does **not** write URL columns — the existing boundary is kept.
2. **Image generation = Gemini in Python** (`google-genai`, `gemini-3.1-flash-image`).
3. **Output = video at 100% + PDF for print.** No separate storybook artifact.
4. **Trigger = Node calls `POST /tributes/{id}/generate`** when the meter hits 100%.

## 3. Flow

**Today:** `/tributes/{id}/generate` → assemble script → write
`latest_generation_context` → push `artifact_generation` → **Node** renders +
S3 + writes `video_url`.

**New:**
```
meter == 100%  →  Node  →  POST /tributes/{id}/generate
                          (presigned: prime_photo_get_url?, video_put_url, pdf_put_url)
  agent: assemble FD-flow book → write latest_generation_context.tribute_video
         status='generating' → push tribute_render queue (ids only)
  ┌───────────────── tribute_render worker (Python, NEW) ─────────────────┐
  │ load row+context → download prime photo (GET url)                       │
  │ Gemini: character ref → opener portrait (photo) + per-beat illustrations│
  │ compose pages (varied layouts, cream blend) → PDF + MP4                 │
  │ upload via PUT urls → status='complete' → NOTIFY tribute_render_complete│
  └────────────────────────────────────────────────────────────────────────┘
  Node: LISTEN tribute_render_complete → write video_url + pdf_url
        UI shows video; "Print" → pdf_url
```

## 4. Story assembly — connected to the FD flow

The story must come from the tribute/FD flow, not raw moments.

**Inputs:**
- **Theme-tagged moments** — moments with a `themed_as` edge to the tribute's
  `theme_id` (the FD theme), falling back to qualifying moments if the theme has
  too few. This is what makes the story "connected to the FD flow."
- **`message_text`** — the contributor's distilled message; placed as the
  emotional **climax**, just before the closing (existing tribute rule).
- **Archetype answers** (the FD theme's `archetype_answers`) — seeded as
  *leads/context* for opener + beat selection. **Never written as facts/moments**
  (invariant #22 / tribute leads rule intact).
- **`ground_truth`** (`scene_subject`) — figure consistency across beats.

**Voice + format** (from the spike, confession register — a loved one telling the
subject's greatness):
- **Opener:** "Meet my {relationship}, …" (one warm sentence) + opener portrait.
- **Beats:** one **8–10-word**, self-contained, contextual line each; logical life
  arc; per-beat `art_direction`; **no eyebrow headers**; rotating layouts.
- **Closing:** a one-sentence conclusion (the message feeds its setup).

**Implementation:** a production assembler in `flashback/tribute/` that emits the
new `Book` shape (opener / beats / closing + art_direction). Prefer extending the
existing `assemble_tribute_script` with a `format="storybook_video"` mode over a
parallel module, so the FD confession voice + climax-placement logic is reused,
not forked.

## 5. Modules — graduate the spike into `flashback/tribute_video/`

| Spike file | Production home | Notes |
|---|---|---|
| `sb/compose.py` | `flashback/tribute_video/compose.py` | template + layouts + blend + text; config-driven zones/colours |
| `sb/art.py` | `flashback/tribute_video/art.py` | Gemini client: character ref, illustrate, portrait_from_photo |
| `sb/video.py` | `flashback/tribute_video/video.py` | layered reveal + Ken Burns + transitions → MP4 |
| `sb/story.py` | merged into `flashback/tribute/assembly.py` | new `format` mode (see §4) |
| `templates/`, `fonts/` | `flashback/tribute_video/assets/` | shipped as package data |

Productionise: `structlog` (no prints), settings-driven constants, typed errors,
no module-level network. New deps in `pyproject.toml`: `Pillow`, `google-genai`,
`imageio`, `imageio-ffmpeg`.

## 6. Worker — `tribute_render`

- **New SQS queue** `tribute_render` (`TRIBUTE_RENDER_QUEUE_URL`).
- **Entry:** `python -m flashback.workers.tribute_render run` — mirrors the
  embedding worker (`Config.from_env`, `run_forever`, SQS long-poll). One render
  per message.
- **SQS payload = identifiers only** (`job_id`, `tribute_id`, `person_id`,
  `composed_at`); Postgres is authoritative (mirrors §3 artifact rule).
- **Steps:** load row + `latest_generation_context.tribute_video` → download prime
  photo (presigned GET) → Gemini (char ref → opener portrait + beats) → compose →
  PDF + MP4 → upload (presigned PUT) → `status='complete'` + counts → transactional
  `NOTIFY tribute_render_complete`.
- **Failure:** SQS visibility timeout sized to the render SLA (render is minutes;
  set visibility ≥ 15 min, or extend mid-render). Persistent failure → DLQ +
  `status='failed'`. Per invariant #25 the DLQ path emits no NOTIFY; Node falls
  back to a timeout.
- **No calls to Node** — presigned URLs arrive up front; completion rides Postgres.

## 7. S3 via presigned URLs (contract)

- `POST /tributes/{id}/generate` request **gains** (Node provides):
  `prime_photo_get_url?` (presigned GET), `video_put_url`, `pdf_put_url`
  (presigned PUT). Expiry must cover queue latency + render (recommend ≥ 24h).
- Agent stores them in `latest_generation_context.tribute_video` (Postgres
  authoritative); the SQS message carries only ids.
- Worker `GET`s the photo and `PUT`s the outputs. It never lists/signs/creates S3
  objects itself.
- **Node writes `video_url` + `pdf_url`** on the completion NOTIFY (it minted the
  keys, so it knows the public URLs). URL-column ownership stays with Node.

## 8. Status, completion, meter

- **Migration 0033:** `tributes.pdf_url TEXT` (Node-written, like `video_url`);
  `status` CHECK adds `'failed'`; optional `rendered_at TIMESTAMPTZ`,
  `render_error TEXT`. Extend `tribute_status` view to expose `pdf_url` (+ the new
  status). No denormalised counters.
- **Completion signal:** `pg_notify('tribute_render_complete', {tribute_id,
  person_id, status, video_present, pdf_present})` inside the worker's final
  transaction — transactional, fires iff commit succeeds (sibling of invariant
  #25). Node `LISTEN`s; the `tributes` row + `tribute_status` view are the truth.
- **Unlock at 100%:** `POST /tributes/{id}/generate` gates on
  `tribute_status.percent >= 100` (meter full) → `409` below it. Removes the old
  "storybook at 50%" unlock; video unlocks at 100%.

## 9. Config

`GEMINI_API_KEY`, `GEMINI_IMAGE_MODEL=gemini-3.1-flash-image`,
`TRIBUTE_RENDER_QUEUE_URL`, and render tunables (page count, fps, durations,
`transition`, `blend`, crf) with env overrides. `google-genai`, `Pillow`,
`imageio`, `imageio-ffmpeg` added to `pyproject.toml`.

## 10. Contract / docs (Node handoff — we don't edit the Node repo)

- **`NODE_INTEGRATION.md`:** tribute render moves to the agent. Node **stops
  consuming `artifact_generation` for `record_type='tribute'`** (still consumes it
  for `moment/entity/thread/person`). Node now: mints presigned URLs on
  `/generate`; `LISTEN`s `tribute_render_complete`; writes `video_url` + `pdf_url`;
  shows the video, "Print" → `pdf_url`; **retires its tribute storybook renderer**.
- **`API.md`:** `/generate` new request fields, the `409` < 100% gate, `pdf_url`.
- **`CLAUDE.md` §3:** scoped exception — the agent renders tribute media and
  reaches S3 **only via Node-minted presigned URLs** (no S3 creds, no URL-column
  writes). Add `tribute_render_complete` as a sibling of invariant #25.

## 11. Out of scope (now)

- The standalone `/storybooks` feature — stays on its current Node-render path.
- Video **edit/regenerate** (v2).
- Audio / music bed.
- All Node-side code (their repo) — documented only.

## 12. Staged implementation

1. **Modules + assembler.** Graduate compose/art/video into
   `flashback/tribute_video/`; add the `storybook_video` assembler mode; unit-test
   the assembler + compositor (no infra). 
2. **Worker + queue + config + deps.** `tribute_render` worker, queue producer at
   `/generate`, settings, `pyproject` deps. DB-test against `TEST_DATABASE_URL`.
3. **Contract + migration + status.** Migration 0033, `tribute_status` view,
   `tribute_render_complete` NOTIFY, presigned-URL fields on `/generate`, 100%
   gate. DB-test.
4. **Docs + handoff.** `NODE_INTEGRATION.md`, `API.md`, `CLAUDE.md` updates;
   retire the tribute `storybook` artifact_kind.

Each phase: build → DB test → manual verify before the next.

## 13. Risks

- **Render cost/time:** ~17 Gemini calls + ffmpeg per tribute. Mitigate: one render
  per SQS message, generous visibility timeout, DLQ, concurrency cap on the worker.
- **Presigned expiry vs queue latency:** require ≥ 24h expiry from Node.
- **ffmpeg on the prod AMI:** `imageio-ffmpeg` bundles a static binary — confirm it
  runs on the EC2 image (ap-south-1).
- **Likeness:** opener portrait from the consented prime photo uses the relaxed
  `COVER_PORTRAIT_NEGATIVE_PROMPT` — same scoped exception as today's cover.
- **Theme-tagged pool too thin:** fall back to qualifying moments so a tribute can
  always assemble.
