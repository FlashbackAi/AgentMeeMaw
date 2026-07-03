# Node Prompt — Storybooks moved to the agent's Python renderer (presigned URLs)

**For:** the Node Backend team.
**Status:** agent side built (Python repo, branch `feat/storybook-python-render`;
migration `0035`, `NODE_INTEGRATION.md` §7c, `API.md` §7c, `CLAUDE.md` §3
updated). Node work outstanding before collection storybooks render.

---

## TL;DR

The standalone `/storybooks` keepsake feature moved **off Node's renderer**
onto the tribute pattern. A storybook is now one of **six fixed collections**
(picked by the user), rendered by the **agent's** `storybook_render` worker as
a **cover PNG + 7 page PNGs + a PDF** composited into that collection's
template, with a **consistent, age-controlled subject likeness** (anchored to
the user's real uploaded photo when available).

Node's job is boundary-preserving: **mint presigned S3 URLs**, call
`POST /storybooks`, **LISTEN** for completion, and **write the URL columns**.
The agent holds **no S3 credentials** and never writes URL columns.

---

## 1. What Node STOPS doing

- **Stop consuming `artifact_generation` messages for storybooks.** The agent
  no longer pushes them from `/storybooks`. (Keep consuming `moment` /
  `entity` / `thread` / `person` — unchanged.)
- **Retire the Node storybook renderer** — the green-punch-through template
  compositor, chroma-key page pipeline, and per-page image calls. Nothing
  consumes them anymore (tributes already moved).
- **Retire the emotional-tag → template mapping.** `storybooks.tags` is
  dormant (column kept, no longer written). Template choice is now the
  user's collection pick.
- The old `POST /storybooks` body (`scope`, `preset`, `tags`) is **gone** —
  see the new contract below.

## 2. Provisioning

- **New SQS queue** `storybook_render` (+ DLQ, `maxReceiveCount=3`,
  visibility timeout generous enough for a full render — **≥ 30 min**
  recommended: one book ≈ 22+ Gemini image calls with lettering re-rolls).
- Agent env: `STORYBOOK_RENDER_QUEUE_URL` (HTTP service), and for the worker
  process: `DATABASE_URL`, `AWS_REGION`, `STORYBOOK_RENDER_QUEUE_URL`,
  `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (gpt-5.1 lettering
  verifier).
- Worker deploy: `python -m flashback.workers.storybook_render run`
  (mirror the `tribute_render` unit; `run-once --storybook-id <uuid>` exists
  for manual replays). **Deploy the worker and set the queue URL together** —
  a set URL with no worker strands rows in `generating`; an unset URL makes
  `/storybooks` return `enqueued:false` (same failure mode the tribute launch
  hit).
- Run migration `0035_storybook_python_render` (adds `collection`, `pdf_url`,
  `page_urls`, `rendered_at`, `render_error`; extends `active_storybooks` and
  your UPDATE grant to `pdf_url` + `page_urls`).

## 3. The handshake (what Node DOES)

```
user picks a collection (GET /storybook-collections drives the chooser)
  →  Node mints presigned URLs: pdf PUT, cover PUT, 7 page PUTs,
     anchor-photo GET (conditional, see §4)
  →  POST /storybooks {person_id, collection, urls...}
     agent: floor-check → context on the row → enqueue storybook_render
  →  worker: curate + write the book (Sonnet) → illustrate (Gemini,
     verified lettering, consistent subject) → PUT pdf + cover + pages
  →  transactional NOTIFY storybook_render_complete
  →  Node writes storybooks.pdf_url + page_urls (+ cover image_url /
     thumbnail_url) from the keys it minted
```

Endpoint details + error semantics (`400` / `404` / `409` "keep sharing
memories" empty-state): `API.md` §7c. Show the 409 detail as the prompt to
tell more stories — that is the eligibility gate.

- **Content types:** PDF `application/pdf`; cover + pages `image/png`.
- **Expiry ≥ 24h** (queue latency + render + retries).
- `page_put_urls` is **ordered** — page 1 first. `pages_present` in the
  NOTIFY says how many were PUT; write that many `page_urls` entries.
- **Regenerate** (`POST /storybooks/{id}/regenerate`): same URL bundle; art
  redrawn, text kept. **Edit** (`/edit`): adds `instructions` +
  `prior_instructions[]` — you keep the cumulative history per record
  (Dynamo), exactly like artifact edits.

## 4. The anchor photo rule (subject likeness)

The illustrated subject is anchored to the person's **real uploaded photo**
when one is in play:

- Read `persons.latest_generation_context` (the profile-picture context).
- `mode == 'with_reference'` → presign a **GET** for its `reference_s3_key`
  and pass it as `anchor_photo_get_url`.
- `mode == 'no_reference'` → **omit the field.** Do not dig up older uploads:
  the latest generation context is the source of truth — a deliberate
  regenerate without a photo means the user chose not to use one.

## 5. Completion — LISTEN, don't poll

Channel **`storybook_render_complete`** (transactional, same guarantees as
`tribute_render_complete` / `extraction_complete`):

```json
{"event": "storybook_render_complete", "storybook_id": "…", "person_id": "…",
 "collection": "childhood", "status": "complete",
 "pdf_present": true, "pages_present": 7, "cover_present": true}
```

On it, write from the keys you minted:

- `storybooks.pdf_url` (when `pdf_present`)
- `storybooks.page_urls` — ordered JSONB array of the first `pages_present`
  page keys (drives the in-app flip-through)
- `storybooks.image_url` + `thumbnail_url` from the cover key (when
  `cover_present`) — the gallery card

Failure: retries exhausted → `status='failed'` + `render_error` on the row,
**no NOTIFY** (nothing to write). Fall back to a UI timeout, same as
extraction/tribute.

## 6. Read surface

`active_storybooks` now exposes `collection`, `pdf_url`, `page_urls`,
`rendered_at` alongside the existing columns. `title` and `script` are
worker-written after assembly (title appears once the book text exists, before
the art finishes).

## 7. Acceptance checklist

- [ ] Queue + DLQ provisioned; `STORYBOOK_RENDER_QUEUE_URL` set on the HTTP
      service **and** the worker deployed (`enqueued:true` on generate).
- [ ] Migration 0035 applied; grants verified
      (`UPDATE (image_url, thumbnail_url, pdf_url, page_urls)`).
- [ ] Chooser driven by `GET /storybook-collections`; URL mint counts follow
      `page_count`.
- [ ] Anchor-photo GET minted per §4 (both modes exercised).
- [ ] LISTEN handler writes the three URL surfaces; flip-through + PDF
      download read from `active_storybooks`.
- [ ] 409 renders as the "keep sharing memories" empty state.
- [ ] Old storybook renderer + `artifact_generation` storybook consumption
      removed.
