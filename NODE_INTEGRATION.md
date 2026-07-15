# NODE_INTEGRATION.md — Wiring the Node Backend to the Agent Service

This is the **handoff brief** for the Node.js Backend (a separate
repo) integrating with this Python agent service. It covers everything
not in `API.md`: service boundaries, auth, transport conventions,
async-timing gotchas, the Postgres read contract, and the
`artifact_generation` SQS contract.

> **Read order for Node engineers**
> 1. This file — boundaries, auth, transport, queues, async timing.
> 2. [`API.md`](./API.md) — request/response shapes for every
>    HTTP endpoint we expose.
> 3. [`SCHEMA.md`](./SCHEMA.md) — column-by-column reference for the
>    Postgres tables Node reads from.
> 4. [`CLAUDE.md`](./CLAUDE.md) §3 (boundaries) and §4 (the 17
>    invariants) — *only* if you need the full rationale. Most of it
>    is summarised here.

---

## 1. Repo split and ownership

```
┌──────────┐     ┌──────────────────┐     ┌──────────────────────┐
│ Frontend │ ──▶ │ Node.js Backend  │ ──▶ │ Python Agent Service │
└──────────┘     │  (your repo)     │     │  (this repo)         │
                 └────┬─────────────┘     └────┬─────────────────┘
                      │                        │
                ┌─────┼─────┐         ┌────────┼─────────────────┐
                ▼     ▼     ▼         ▼        ▼                 ▼
            DynamoDB  S3  Postgres  Postgres  Valkey      SQS (4 queues)
            (Node)  (Node) (read)   (write)   (agent)     extraction
                                                          embedding
                                                          artifact_generation
                                                          others
```

### Node owns

- Auth and users. Multi-contributor `person_roles` are deferred in v1.
- **Onboarding / legacy creation.** Collecting the subject's name,
  relationship, gender, contributor name, optional photo, and running
  the archetype question flow before the first conversation begins.
  Node owns the authenticated UX; the agent owns `persons` creation
  plus archetype answer processing.
- Sessions and per-turn transcript log → DynamoDB.
- **All user-facing reads** from Postgres for the legacy review UI
  (moments, entities, threads, traits, profile facts, profile
  summary). Node has direct read-only Postgres access.
- **Consuming the `artifact_generation` SQS queue** — calling the
  image / video model, uploading to S3, writing the URL columns
  (`image_url`, `video_url`, `thumbnail_url`) back to Postgres.

### Agent service (us) owns

- Conversation: opener, per-turn replies, segment detection.
- All writes to the canonical graph (moments, entities, threads,
  traits, questions, edges, profile facts).
- Working memory in Valkey.
- Producing onto all SQS queues, including `artifact_generation`.
- The embedding worker (drains `embedding`, writes vector columns).

### Hard rule — the frontend never talks to the agent directly

Every call to the agent service goes **Frontend → Node → Agent**. The
agent has no CORS surface, no public ingress, and no per-user auth.
Network topology should enforce this: the agent lives on a private
subnet, reachable only from Node. Treat any frontend code that knows
the agent's URL or its service tokens as a bug — the tokens are
service-to-service secrets and must never leave the Node process.

This holds in local dev, staging, and production without exception.

### Hard rules — Node MUST NOT

- Touch DynamoDB transcript bookkeeping in our service paths (we never
  read it; pass anything we need on the request).
- Write to canonical graph tables — `persons`, `moments`, `entities`,
  `threads`, `traits`, `questions`, `edges`, `profile_facts`,
  `identity_merge_suggestions`, history tables. Reads only.
- Write to the `narrative_embedding`, `description_embedding`,
  `embedding_model`, `embedding_model_version` columns. The embedding
  worker owns those.
- Write to `generation_prompt`. The agent writes it; Node reads it.
- Mutate `status`. If the UI needs to "delete" or "edit" a node, call
  the agent's edit endpoint (see `API.md` §7).
- Push onto `extraction`, `embedding`, or any queue **except**
  consuming `artifact_generation`.

If the UI needs a write the agent doesn't currently expose, talk to
us — we'll add an endpoint. Don't reach for raw SQL.

---

## 2. Authentication

There is **no per-user auth** between Node and the agent. The agent
trusts the network plus a shared service token. **Node is the auth
boundary** — by the time a request hits the agent, Node has already
verified the user.

### Tokens

| Token | Purpose | Required for |
|---|---|---|
| `SERVICE_TOKEN` | Service-to-service shared secret | Every agent endpoint except `/health` |
| `ADMIN_SERVICE_TOKEN` | Privileged operations | `/admin/*` (in addition to `SERVICE_TOKEN`) |

Both are set in the agent's environment and must be **different
values** — the agent rejects boot if they match.

### Headers

```
X-Service-Token:        <SERVICE_TOKEN>
X-Admin-Service-Token:  <ADMIN_SERVICE_TOKEN>   # only for /admin/*
```

Token comparison is constant-time (`secrets.compare_digest`). Mismatch
or missing → `401 Unauthorized`.

### How Node should source these

Store both tokens in Node's secret manager (whatever you use today —
AWS Secrets Manager, env, etc.). They are **never** exposed to the
frontend, never embedded in a client bundle, never returned in any
Node response body. The frontend has no business knowing the agent
exists. Rotate by deploying both services together with new values;
there is no in-flight rotation protocol.

For local dev, the agent honours `SERVICE_TOKEN_AUTH_DISABLED=1` —
both header checks are skipped. Don't ever set this in staging or
production.

### Why no JWT / per-user

The agent has no user model. It accepts `person_id` and `role_id` on
every conversational request and trusts that Node has authorised the
caller to see that legacy. If the user is wrong, Node is wrong.

---

## 3. Transport conventions

### Base URL

Set per environment. The agent listens on whatever port `uvicorn`
binds to (default `8000`). Behind a private network — never publicly
exposed.

```
AGENT_BASE_URL=https://agent.internal.flashbacklabs.com   # example
```

### Request / response

- `Content-Type: application/json` on every request and response.
- Bodies use `extra="forbid"` (pydantic) — unknown fields → `422`.
- UUIDs are canonical strings (`8-4-4-4-12`).
- Timestamps are ISO-8601 with timezone (UTC).

### Timeouts (recommended)

| Endpoint | Suggested client timeout |
|---|---|
| `GET /health` | 5s |
| `POST /session/start` | 30s — runs phase gate + LLM opener |
| `POST /session/start/stream` | 60s (overall); 15s to first byte |
| `POST /turn` | 45s — intent + retrieval + response LLM |
| `POST /turn/stream` | 90s (overall); 20s to first byte |
| `POST /session/wrap` | 60s — segment flush + summary LLM |
| `POST /nodes/.../edit` | 45s — edit-LLM call |
| `POST /identity_merges/scan` | 60s — small LLM per candidate |
| All other writes | 15s |

For the streaming endpoints, configure your HTTP client so the
**overall** timeout is long (streams can legitimately stay open for
tens of seconds) but the **time-to-first-byte** check is tight — that's
the signal that the upstream LLM stalled before producing anything.

These are *client* timeouts. The agent has its own server-side LLM
timeouts; on hitting them it returns `504` for `/nodes/.../edit` and
`503` for other LLM-driven calls.

### Retries

The agent is **not** retry-safe by default. Endpoints that accept
`Idempotency-Key` are; others are not.

| Endpoint | Idempotency-Key supported | Safe to retry blind? |
|---|---|---|
| `POST /turn` | yes | only with same key |
| `POST /session/wrap` | yes | only with same key |
| `POST /identity_merges/.../approve` | yes | only with same key |
| `POST /nodes/.../edit` | yes | only with same key |
| `POST /session/start` | no | no — would create a duplicate opener turn |
| `POST /turn/stream` | no | no — partial reply already committed; let the user continue instead |
| `POST /session/start/stream` | no | no — would create a duplicate opener turn |
| `POST /profile_facts/upsert` | no | yes — natural idempotency: identical `answer_text` is a no-op |
| `POST /identity_merges/scan` | no | no — scan can be re-run, but it's a costly LLM fan-out |
| `POST /admin/reset_phase` | no | yes — single SQL statement, deterministic |

**Idempotency key rules.** Use a UUIDv4 generated client-side per
logical operation. Reusing the same key returns the exact prior
response within 24h, scoped per `(operation, primary key)`. A second
request with the same key while the first is still in flight gets a
`409 Conflict` (treat as transient: backoff + retry the *same* key).

### Errors

See `API.md` §1 for the full status-code table. Two patterns to know:

```json
{ "detail": "human-readable message" }
```

vs the LLM/dependency-failure shape:

```json
{ "error": "service_unavailable", "detail": "..." }
```

In Node, treat `5xx` as transient (retry with backoff if idempotent),
`4xx` as terminal (surface to the user or log as a bug).

### Streaming (SSE) endpoints

Two endpoints expose the assistant text as Server-Sent Events so the
frontend can render tokens as they arrive instead of waiting for the
full JSON response:

- `POST /turn/stream` — streaming twin of `/turn`
- `POST /session/start/stream` — streaming twin of `/session/start`

The non-streaming JSON variants are unchanged and still supported.
Migrate per route; nothing forces you to convert both at once.

**Wire format.** Each response is `Content-Type: text/event-stream`,
status `200`, with named events in this order:

1. exactly one `meta` event — pre-LLM metadata available before the
   LLM call begins (intent, taps, question chips on `/turn/stream`;
   phase, selected question, chips on `/session/start/stream`).
2. zero or more `text_delta` events — `{"text": "..."}` chunks.
   Append in arrival order; no pre-trim on individual chunks.
3. exactly one terminal event:
   - `done` on success — carries the assembled `reply`/`opener` text
     plus post-LLM bits like `segment_boundary`.
   - `error` on failure — `{"code", "message", "partial_text"}`.

Full payload shapes live in `API.md` §3 under `POST /turn/stream` and
`POST /session/start/stream`.

**What Node does with each event:**

- `meta` — render chip rows / tap cards in the UI immediately so they
  are on screen by the time text arrives. Don't wait for `done`.
- `text_delta` — append to the visible assistant bubble. Don't try to
  normalise whitespace per chunk; do it once on `done`.
- `done` — persist the per-turn transcript entry to DynamoDB using
  the `reply`/`opener` field. Replace the streamed UI text with the
  canonical (whitespace-stripped) version if you want exact parity
  with what the agent stored to working memory.
- `error` — close the UI bubble. If `partial_text` is non-empty, keep
  it visible; the agent has already written it to working memory so
  the next turn will treat the conversation as having received that
  partial reply. Surface a soft error to the user ("connection
  hiccup — keep going") rather than retrying.

**Idempotency.** Not supported on the streaming endpoints. Do not
retry on disconnect — partial assistant text is committed on the
agent side, so the next `/turn` or `/turn/stream` call will pick up
naturally.

**Disconnect handling.** If the frontend disconnects mid-stream,
cancel the upstream Node→Agent connection. Closing the TCP socket is
sufficient; the agent does not need a separate notification. Any
text that streamed before the disconnect is already in working
memory.

**Re-emit to the frontend.** The simplest path is pass-through SSE:
re-emit each agent event to the frontend with the same `event:` name
and `data:` payload, after flushing. If your frontend wants a
different transport (WebSocket, your existing realtime channel),
translate event-by-event — do **not** buffer the agent stream and
re-emit only at `done`.

**Auth + frontend session validation** runs unchanged before opening
the upstream connection. The agent's `X-Service-Token` is added on
the Node→Agent leg, never exposed to the browser.

**HTTP client recommendations:**

- `undici` (Node 18+ built-in) — body is an async iterator, fastest
  zero-copy path.
- `node-fetch` v3+ — also fine.
- `axios` — set `responseType: 'stream'`, disable automatic retry.

Parse SSE with `eventsource-parser` rather than hand-splitting on
`\n` — the spec allows `\r\n` line endings, multi-line `data:`
fields, and comment lines that begin with `:`.

**Backpressure.** The frontend's read rate eventually backpressures
into the Node→Agent connection. That's fine — the agent will pause
the LLM stream until your reader catches up. Don't add buffering on
either leg.

---

## 4. Onboarding — Node-owned UX with agent archetype processing

Before the first call to `/session/start`, Node must complete the
legacy creation flow:

1. Collect the subject's `name`, the contributor's `relationship` to
   the subject, subject `gender`, the contributor's display name, and
   optional photo / reference image.
2. Call `POST /persons` on the agent to create the status-agnostic
   `persons` row. Do not collect DOB / DOD; lifespan emerges from
   stories and time anchors later.
3. Call `GET /api/v1/onboarding/archetype-questions?person_id=...` and
   show the returned tappable questions. The response does not
   expose server-side `implies` blocks. Questions with
   `allow_multiple: true` (all except the two ground-truth questions)
   render toggleable chips — the user can pick several.
4. Call `POST /api/v1/onboarding/archetype-answers` with `person_id`
   and one answer per returned question. Chips go in `option_ids`
   (any number) and may combine with `free_text` on the same answer;
   the legacy single `option_id` is still accepted. `skipped: true`
   stands alone. Questions with `allow_multiple: false` keep the old
   exactly-one-of rule.
5. Use the returned `session_id` for the immediate `/session/start`
   call. The agent stores `persons.archetype_answers` and uses it for
   the first opener without requiring Node to maintain a role table.
6. Push or otherwise trigger the person's portrait artifact only when
   you have enough visual material for a useful prompt. The agent's
   `POST /persons` row creation intentionally does not enqueue a thin
   name-only portrait prompt.

`persons.onboarding_complete` gates resume behavior. If it is
`false`, resume the archetype question step; if it is `true`, go
straight to chat. The agent returns `409 Conflict` from the archetype
question/answer endpoints when onboarding is already complete.

The archetype endpoints are service-to-service only, same as the rest
of the agent API. Node remains the user-auth boundary: verify the user
owns the role before calling the agent.

---

## 5. Session lifecycle from Node's perspective

This is the choreography for one user conversation.

```
Frontend                Node                              Agent
   │                     │                                  │
   │  open conversation  │                                  │
   ├────────────────────▶│                                  │
   │                     │  POST /session/start             │
   │                     │  { session_id, person_id,        │
   │                     │    role_id, session_metadata }   │
   │                     ├─────────────────────────────────▶│
   │                     │  200 { opener, metadata }        │
   │                     │◀─────────────────────────────────┤
   │  "opener"           │                                  │
   │◀────────────────────┤                                  │
   │                     │                                  │
   │  user message       │                                  │
   ├────────────────────▶│                                  │
   │                     │  POST /turn                      │
   │                     ├─────────────────────────────────▶│
   │                     │  200 { reply, metadata }         │
   │                     │◀─────────────────────────────────┤
   │  reply + tap chips  │                                  │
   │◀────────────────────┤                                  │
   │      … (loop) …     │                                  │
   │                     │                                  │
   │  user closes / idle │                                  │
   ├────────────────────▶│                                  │
   │                     │  POST /session/wrap              │
   │                     ├─────────────────────────────────▶│
   │                     │  200 { session_summary, ... }    │
   │                     │◀─────────────────────────────────┤
```

### `session_id`

For first-time onboarding, use the `session_id` returned by
`POST /api/v1/onboarding/archetype-answers`. For later sessions, Node
generates a fresh UUID. It is stable for the duration of one
conversation and used by the agent as the working-memory key.

### `contributor_display_name`

Optional but **recommended on every session**. Used for attribution
in **archive-side generated text only** — entity descriptions, moment
narratives, thread summaries, profile summary, profile facts. The
review UI ends up reading naturally ("John, Sarah's father, was a
carpenter") instead of generic ("the contributor's father").

The first opener may use the contributor name as relationship context,
but the agent should not use it as a repeated salutation. `/turn`
replies stay relationship-centered rather than name-heavy.

The contributor's name is collected at onboarding (§4) and is
Node-side state — pass it on every `/session/start`. The agent does
not persist it across sessions today; this is single-contributor
scope only. Multi-contributor architecture is deliberately deferred.

### `session_metadata.prior_session_summary`

Optional. If you have a prior session summary for this person, pass
it on `/session/start`. The agent seeds it into working memory as a
read-only field that the Response Generator consults; **extraction
ignores it**. If you don't have one, omit the key. Don't fabricate.

### `session_metadata.archetype_answers`

Optional on the first session after archetype onboarding. The agent
already stores `persons.archetype_answers` during
`POST /api/v1/onboarding/archetype-answers`; if Node passes the same
array in metadata, the first-time opener uses it directly. Either way, the
first opener anchors on the most concrete captured detail and avoids
re-asking anything the contributor already tapped or typed.

### Turn `metadata.taps`

`/turn` responses always include `metadata.taps` as a list. Render each
tap as a chip beneath the bot reply. When the contributor taps a chip,
POST `/turn` normally with the chip text as `message`; no special Node
field is required. `/session/start` also includes `metadata.taps`, but
it is always `[]` in v1.

### `/session/wrap` is mandatory

The unflushed tail of the conversation (open segment) only gets pushed
onto the extraction queue at `/session/wrap`. **A session that never
gets wrapped will silently lose its trailing turns.**

Call wrap when:
- The user closes the conversation explicitly.
- The session has been idle past your inactivity threshold.
- You're tearing down the conversation surface for any reason.

It is safe to call wrap on a session that has zero new turns since the
last segment boundary — the agent no-ops the extraction push but still
returns a session summary.

### Working memory expiry

Working memory has a TTL (default ~24h). If the session is wrapped,
working memory is cleared immediately. If not, it expires on its own.
Calling `/turn` after working memory is gone returns `409`. Treat that
as "session expired; start a new one."

### Per-session rate limit

`/turn` is rate-limited per `session_id` (default 60/min). Respect
`429` with backoff; don't retry instantly.

---

## 6. Reading the canonical graph from Postgres

Node has direct **read-only** access to the agent's Postgres. This is
the contract for those reads.

### 6.1 Always filter `status = 'active'`

Or, equivalently, query the `active_*` views (`active_moments`,
`active_entities`, `active_threads`, `active_traits`,
`active_questions`, `active_edges`, `active_profile_facts`). **Never
read base tables without a status filter** — `superseded` and `merged`
rows are kept for history, not display.

For `persons` there is no status; `active_persons` exists for
symmetry but is just `SELECT * FROM persons`.

### 6.2 Always filter `person_id`

Every read for a single legacy must scope by `person_id`. Don't let a
query cross legacies — the agent assumes that boundary holds.

### 6.3 Embedding model column

Vector columns (`narrative_embedding`, `description_embedding`,
`answer_embedding`) are paired with `embedding_model` and
`embedding_model_version`. Node typically doesn't read vectors, but if
you ever do (e.g. similarity in the UI), filter by the model + version
the agent currently writes — see [`config.py:embedding_model`](src/flashback/config.py).
Mixing rows across models gives garbage.

### 6.4 Tables Node reads, by surface

| UI surface | Tables / views |
|---|---|
| Legacy profile header | `persons`, `active_profile_facts` |
| Moments timeline | `active_moments`, `active_edges` (for `involves`/`happened_at`) |
| Entity pages | `active_entities`, `active_edges` |
| Threads | `active_threads`, `active_edges` (for moments in thread) |
| Traits | `active_traits` |
| Open questions / "ask next" (raw) | `active_questions` (filtered by status / answered_by edges) |
| Question **feed** (ranked browse surface) | `GET /questions/feed?person_id=...` — agent-ranked; do **not** re-derive from the view |
| Identity merge review | `identity_merge_suggestions` (the GET endpoint is more convenient) |

**Question feed → tap to start.** For the scrolling feed, call
`GET /questions/feed?person_id=...` (ranked, producer-bank only,
`skip`/`suppress` filtered, `universal_dimension` spread) and render the
`questions[]`. When the contributor taps one, start a session with that
question's `question_id` in `session_metadata.question_id` on
`POST /session/start` — the agent's opener anchors on it. No new write
surface: this is a read + an existing metadata field.

### 6.5 Columns Node writes

Strictly limited:

| Table | Columns Node may write |
|---|---|
| `persons` | `image_url`, `thumbnail_url` |
| `moments` | `video_url`, `thumbnail_url` |
| `entities` | `image_url`, `thumbnail_url` |
| `threads` | `image_url`, `thumbnail_url` |
| `tributes` | `video_url`, `pdf_url`, `image_url`, `thumbnail_url` |

These are the artifact URL columns. Node writes them after the
artifact-generation worker uploads to S3 — and, for `tributes`, after the
agent's `tribute_render` worker PUTs the MP4/PDF to your presigned URLs and
fires `tribute_render_complete` (§7b). **Nothing else.**

In particular, **do not** write `generation_prompt` (we write it),
`status`, `superseded_by`, `merged_into`, embedding columns, or any
non-URL field.

---

## 7. The `artifact_generation` SQS queue (Node consumes)

This is the only queue Node consumes from. The agent pushes one
message per artifact-bearing row whenever a new `generation_prompt` is
written (extraction, edit, identity-merge survivor refresh).

### Payload

```json
{
  "record_type":       "person | moment | thread | entity",
  "record_id":         "<uuid>",
  "person_id":         "<uuid>",
  "artifact_kind":     "image | video",
  "generation_prompt": "<one-sentence visual description>"
}
```

`artifact_kind` mapping:
- `moment` → `video` (with thumbnail)
- `person`, `entity`, `thread` → `image` (with thumbnail)

### What Node should do per message

1. Look up the row by `(record_type, record_id, person_id)` to fetch
   the latest `generation_prompt`. **Don't trust the prompt in the
   message body** — it's a snapshot; an edit may have superseded it
   while the message was queued. The Postgres value wins.
2. Verify `status = 'active'` (for tables that have status). If the
   row is now `superseded` or `merged`, ack the message and skip — a
   newer message for the survivor is already (or about to be) on the
   queue.
3. Call your image / video generation model with the prompt.
4. Upload to S3.
5. UPDATE the row's `image_url` / `video_url` + `thumbnail_url`.
6. Ack the message.

### Idempotency on Node's side

The agent may push duplicate messages — at-least-once is normal for
SQS. Node's consumer should be idempotent: regenerating the same
artifact for the same `(record_type, record_id)` and overwriting the
URL columns is the expected steady state.

If you want to dedupe, key on `(record_type, record_id,
generation_prompt)` — same prompt, same artifact, skip. Different
prompt is the signal that the agent re-extracted or the contributor
edited.

### Failure handling

Use SQS DLQ for persistent failures. The agent does not poll the DLQ;
surface DLQ depth in your own monitoring.

### Father's Day storybook cover (reference image + de-age)

For the Father's Day skin (`campaign = "fathers_day_2026"`), the
storybook `latest_generation_context.storybook.cover` may now carry
`reference_s3_key` (a contributor-uploaded photo of the subject),
`hero_line` (optional secondary cover text), and a **relaxed**
`negative` (`COVER_PORTRAIT_NEGATIVE_PROMPT` — the no-likeness ban is
dropped **for the cover only**; page art keeps the full ban). When
`reference_s3_key` is present, render the cover **image-to-image** from
that photo; when absent, the existing establishing-scene cover behavior
applies. Pass the photo to the agent via the new optional
`prime_photo_s3_key` field on `POST /tributes/{id}/generate`. Full
contract: **`docs/STORYBOOK_FD_COVER_NODE_PROMPT.md`**.

---

## 7b. Tribute video — now rendered by the agent (NOT via artifact_generation)

The tribute output moved off Node's renderer. A tribute now produces a **video**
(shown in-app) + a **PDF** (print on request), rendered by the agent's
`tribute_render` worker. The standalone tribute **storybook** artifact is
retired: `POST /tributes/{id}/generate` with `artifact_kind='storybook'` returns
**410**. (The separate `/storybooks` keepsake-book feature is unaffected.)

### What Node STOPS doing

- **Do not** consume `artifact_generation` messages with `record_type='tribute'`
  — the agent no longer pushes them. Keep consuming `moment` / `entity` /
  `thread` / `person` (unchanged, §7).
- Retire the tribute storybook renderer (templates / compositor / image model)
  for tributes.

### Reaching 100% — the message card (no chat needed, 2026-07-15)

When the meter shows the **message as the only unfilled slot**, show the
question (the `message` slot's `hint` — now fully resolved
campaign → relationship-profile → neutral) directly on the tribute card
with a text box, and submit it via the new
`POST /tributes/{id}/message` `{person_id, text}` — the response is the
fresh progress payload (same shape as `GET /tributes/{id}/progress`),
typically `percent: 100, ready: true` → reveal the Generate button. The
in-chat card now fires at most once per session (warm moment only); the
every-2-turns re-ask is retired. Full contract:
`docs/TRIBUTE_MESSAGE_CARD_NODE_PROMPT.md`.

### The new handshake — Node mints presigned URLs; the agent renders

1. When the meter hits **100%** (`tribute_status.percent = 100`), call
   `POST /tributes/{id}/generate` with `artifact_kind='tribute_video'` and:
   - `video_put_url` — presigned **PUT** for the MP4 (`video/mp4`)
   - `pdf_put_url` — presigned **PUT** for the PDF (`application/pdf`)
   - `poster_put_url` — presigned **PUT** for the cover poster (`image/jpeg`)
     (optional but recommended; when present the worker PUTs the opener page —
     the cover: portrait + title — as a JPEG, and you write `thumbnail_url` from
     the key on completion so the tribute card/thumbnail shows the cover, not a
     stray video frame)
   - `prime_photo_get_url` — presigned **GET** for the contributor's prime photo
     (optional; when present the opener becomes a painterly portrait of the
     subject, image-to-image, likeness kept)

   Expiry must cover queue latency + render — **≥ 24h** recommended. Below 100%
   → **409**; missing PUT URLs → **400**. Sign the PUTs for the content-types
   above (or sign without enforcing content-type).
2. The agent assembles the FD-flow story, stores it + your URLs on the row,
   flips `status='generating'`, and enqueues the render. Response is the usual
   `{job_id, percent, ready, enqueued, ...}`.
3. The agent's worker downloads the photo (your GET URL), renders the MP4 + PDF,
   and **PUTs** them to your URLs. **No AWS credentials live in the agent** — it
   only uses the URLs you sign, and it never writes the URL columns.

### Completion — listen, don't poll (mirrors `extraction_complete`, §8.3)

- The worker fires a **transactional** `NOTIFY` on channel
  **`tribute_render_complete`** on success:
  ```json
  {"event":"tribute_render_complete","tribute_id":"…","person_id":"…",
   "status":"complete","video_present":true,"pdf_present":true,
   "poster_present":true}
  ```
- On that signal, **Node writes `tributes.video_url` + `tributes.pdf_url`** from
  the keys it minted (you signed the PUTs, so you know the object keys), then
  shows the video; "Print" → `pdf_url`. When `poster_present` is true and you
  minted a `poster_put_url`, also write `tributes.thumbnail_url` from the poster
  key so the tribute card/thumbnail shows the cover (the opener page) rather
  than a stray video frame.
- The `tributes` row + the `tribute_status` view are authoritative. `status`
  goes `generating → complete`, or `failed` if the render exhausts SQS retries —
  the DLQ path emits **no** NOTIFY, so fall back to a timeout (same as
  extraction). `tribute_status` now also exposes `pdf_url` + `rendered_at`.

**Why presigned (not agent-side S3):** S3 + URL-column ownership stay with Node
(CLAUDE.md §3). The agent renders the bytes; Node owns storage.

### 7b.1 The completion meter (decorated) — `GET /tributes/{id}/progress`

The meter that gates the 100% → `/generate` step is the **decorated** progress
(per-slot label/hint, campaign `title`, `next` steer) — the same block `/turn`
emits as `tribute_progress`. It's now also a **standalone read** so the UI can
refresh the meter without a chat turn (after an upload, on modal open, light
polling). Add a Node route that proxies
`GET /tributes/{id}/progress?person_id=<uuid>&campaign=<slug?>` (derive
`person_id` server-side; forward `campaign` when skinned) and pass the body
through. Full Node-side spec: `docs/TRIBUTE_PROGRESS_ENDPOINT_NODE_PROMPT.md`;
shape: `API.md` §7b. This is the **meter only** — render status (`video_url` /
`pdf_url` / `rendered_at`) stays on the `tribute_status` view you read directly.

---

## 7c. Storybooks — now rendered by the agent (NOT via artifact_generation)

The standalone `/storybooks` keepsake feature moved off Node's renderer onto
the same Python-render pattern as tributes (spec
`docs/superpowers/specs/2026-06-29-storybooks-python-render-design.md`). A
storybook is now one of **six fixed collections** rendered as a
**cover + 7 page PNGs + a PDF**. Node-side work order:
**`docs/STORYBOOK_PYTHON_NODE_PROMPT.md`**.

### What Node STOPS doing

- **Do not** consume `artifact_generation` messages for storybooks — the agent
  no longer pushes them from `/storybooks` (moment / entity / thread / person
  jobs are unchanged, §7).
- Retire the Node storybook renderer (templates / compositor / image calls)
  and the emotional-tag → template mapping (`storybooks.tags` is dormant).

### The new handshake — Node mints presigned URLs; the agent renders

1. `GET /storybook-collections` returns the fixed registry:
   `[{slug, display_name, layout, page_count}]` (page_count is 7 for all six).
   Drive the chooser from it and mint URLs per its counts. **Pass
   `?person_id=<uuid>`** (design 2026-07-06) to also get `tagged_count` +
   `eligible` per collection — render locked cards with a "3/5 stories" badge
   and only enable a collection when `eligible` is true. Eligibility is a
   deterministic count of qualifying moments tagged to the collection (grid
   floor 5; `wisdom` counts the whole pool, floor 3), so no LLM runs.
1b. **Optional pick-your-moments preview** (spec 2026-07-05; tag-scoped
   2026-07-06). Before minting anything, `POST /storybooks/preview` with
   `{person_id, collection}` returns
   `{collection, bounds:{min_select,max_select}, moments:[{id, title,
   snippet, life_period, picked, collections, suggested_collection,
   used_in}]}`. **Two-tier:** `picked: true` are the collection's tagged
   moments (for `wisdom`, the whole pool), first and deterministic — no LLM
   curation, so the preview is **instant**. After them the rest of the
   person's whole qualifying pool is listed `picked: false` so the family can
   *add* a moment the tagger didn't put in this collection. Render as a
   checklist with `picked` pre-selected; show each moment's `collections`
   chips so an added out-of-collection moment is visible as such
   (`suggested_collection` is the deprecated single-hint form). `used_in` is
   an "also appears in X" chip — informational, never blocking. Enforce
   `bounds` client-side (disable confirm outside min/max). The call is
   **stateless and read-only**: nothing persists until create. Errors: 400
   unknown collection, 404 person, 409 too few *tagged* moments (the addable
   remainder doesn't count toward eligibility). Skipping this step keeps the
   auto-select flow.
2. `POST /storybooks` with `{person_id, collection, pdf_put_url,
   cover_put_url, page_put_urls[7], anchor_photo_get_url?, moment_ids?}`:
   - `moment_ids` — the confirmed selection from step 1b (≤64, deduped;
     ids must be qualifying moments for the person — validated against the
     **whole** qualifying pool, not just the collection's tagged slice, so
     adds of out-of-collection moments are allowed — else **400**; count
     within `bounds` else **409**; the collection must still be eligible by
     tag count or create **409**s before selection is considered). Omit it to
     auto-select the collection's tagged pool deterministically. When present
     the worker renders from exactly this slice and regenerate/edit preserve it.
   - `pdf_put_url` — presigned **PUT** (`application/pdf`)
   - `cover_put_url` — presigned **PUT** (`image/png`)
   - `page_put_urls` — exactly **7** presigned **PUT**s (`image/png`), in page
     order
   - `anchor_photo_get_url` — presigned **GET** for the subject's real photo.
     **Mint it from `persons.latest_generation_context` when its `mode` is
     `with_reference`** (sign that context's `reference_s3_key`); omit when
     `no_reference`. The latest generation context is the source of truth — a
     deliberate regenerate without a photo means "don't use the old photo".
   Expiry ≥ 24h. Unknown collection / wrong URL count → **400**; person not
   found → **404**; too few qualifying moments → **409** with a user-facing
   "keep sharing memories" detail (show it as the empty-state prompt).
3. The agent stores the render context on the row (`status='generating'`,
   `collection` set) and enqueues `storybook_render`
   (env: `STORYBOOK_RENDER_QUEUE_URL`). Response:
   `{job_id, storybook_id, person_id, collection, status, source,
   moments_count, enqueued}`.
4. The worker curates + writes the book (Sonnet), renders illustrations
   (Gemini, one consistent age-controlled subject; lettering verified), PUTs
   the PDF + cover + 7 pages to your URLs, and writes `title` + `script` on
   the row. **No AWS credentials live in the agent.**
5. `POST /storybooks/{id}/regenerate` (same URL bundle) redraws the art with
   the stored script; `POST /storybooks/{id}/edit` adds
   `{instructions, prior_instructions[]}` (you keep the cumulative history,
   as with artifact edits) and re-assembles the text.

### Completion — listen, don't poll (mirrors `tribute_render_complete`)

- Transactional `NOTIFY` on channel **`storybook_render_complete`**:
  ```json
  {"event":"storybook_render_complete","storybook_id":"…","person_id":"…",
   "collection":"childhood","status":"complete","pdf_present":true,
   "pages_present":7,"cover_present":true}
  ```
- On that signal, **Node writes** `storybooks.pdf_url`, `storybooks.page_urls`
  (ordered JSONB array of the `pages_present` page keys you minted) and — when
  `cover_present` — `image_url`/`thumbnail_url` from the cover key. The
  `active_storybooks` view now exposes `collection`, `pdf_url`, `page_urls`,
  `rendered_at` for the gallery + flip-through.
- `status` goes `generating → complete`, or `failed` (+`render_error`) when
  retries exhaust — the DLQ path emits **no** NOTIFY; fall back to a timeout.

---

## 8. Async timing — gotchas Node needs to know

### 8.1 Embeddings are not synchronous

When a write returns 200 from the agent (e.g. `POST /nodes/{moment}/{id}/edit`),
the new row is in Postgres but its `narrative_embedding` is **null**
until the embedding worker runs. Latency: typically seconds, but not
guaranteed.

**UI implication.** If your UI surfaces "search this person's
moments" or any vector-based view, a freshly-edited row may not appear
in similarity results immediately. For exact / list / fact views (the
common case), this doesn't matter — the row is fully readable.

### 8.2 Artifacts are not synchronous

Same pattern: the agent writes `generation_prompt` synchronously and
pushes the SQS message. The actual `image_url` / `video_url` is filled
in by Node's own worker. The UI should treat artifact URLs as
"eventually present, may be null."

For new moments/entities, render a placeholder until the URL appears.
For edits, the **old** URL is still on the record until your worker
overwrites — that's a feature, not a bug. The UI shouldn't flash empty.

### 8.3 Extraction completes asynchronously — listen, don't poll

`/session/wrap` returns after generating the session summary and
pushing the unflushed (tail) segment onto the `extraction` queue. The
actual moments, entities, traits, etc. land in Postgres **later**, as
the Extraction Worker drains each segment (then the Trait Synthesizer →
Profile Summary chain runs).

**Do not poll for the appearance of moment rows.** A segment can
legitimately extract **zero** moments (under-extraction, agent
invariant #6), so "no row yet" is ambiguous between "still running" and
"nothing will ever come" — no polling interval resolves that.

Instead, the agent emits a Postgres `NOTIFY` on channel
**`extraction_complete`** inside the extraction transaction, once per
segment. Payload (JSON — identifiers + convenience counts; Postgres is
authoritative):

```json
{
  "event": "extraction_complete",
  "session_id": "…",
  "person_id": "…",
  "segment_message_id": "…",
  "is_final": true,
  "status": "done",
  "moments_written": 3
}
```

**Node integration:**
- Hold one dedicated `LISTEN extraction_complete` connection — a
  direct/session-pinned connection. A transaction-mode pooler (e.g.
  PgBouncer) silently drops `LISTEN`; the listener must bypass it.
  Listen on the same database the agent writes to (the canonical-graph
  DB you already read).
- Treat the `NOTIFY` as a wake-up only. Read the authoritative set from
  the **`session_extraction_status`** view (`WHERE session_id = …`),
  aggregating `sum(moments_written)` and `bool_or(is_final)`.
- `is_final = true` marks the session's wrap-forced tail segment — the
  cue to render the final "session complete, N new moments" state
  (N may be 0).
- **Durability backstop:** on listener (re)connect, re-query
  `session_extraction_status` for any rows newer than your last-seen
  `processed_at` watermark, to catch notifications missed while
  disconnected. The `NOTIFY` is fire-and-forget; the row is the truth.
- A segment that permanently fails extraction (lands in the DLQ) emits
  **no** notification. If no `is_final` arrives within your wrap
  timeout, surface "still processing" and re-query.

### 8.4 Phase transitions are sticky and asynchronous

A person flips from `phase='starter'` to `'steady'` after the
Coverage Tracker sees coverage in all 5 anchor dimensions. That
happens inside the Extraction Worker, after a session ends. The UI
will see the new phase on the next page load.

To force a person back to `starter` (debugging, demo reset), call
`POST /admin/reset_phase` with the admin token. There is no
Node-driven path that should ever flip phase the other way.

---

## 9. The edit surface (`POST /nodes/{type}/{id}/edit`)

This is the only write path the legacy review UI needs. v1 supports
`moment` and `entity`. Full schema in `API.md` §7; integration notes:

### What to send

The contributor's revised prose for the **primary text field** —
`narrative` for moments, `description` for entities. The agent's
edit-LLM re-derives the structured fields from this text.

Don't send a JSON patch; don't try to set individual columns. Send
prose.

### What the response means

```json
{
  "node_type": "moment",
  "node_id": "<NEW uuid for moments, SAME uuid for entities>",
  "superseded_id": "<previous uuid for moments, null for entities>",
  ...
}
```

For **moments**, the edit is a supersession — `superseded_id` is now
`status='superseded'`, and `node_id` is the new active row. Update
your UI's stable identifier to the new `node_id`.

For **entities**, the edit is in-place — `node_id` is unchanged.

### When to refresh

After a 200, re-query the affected row(s). Embeddings and artifact
URLs will follow asynchronously — see §8.1 and §8.2.

### Concurrency

Entity edits are protected by an optimistic lock: if two edits race,
one returns `409 Conflict`. Surface it to the user as "this entity
changed; please refresh and re-edit."

Moment edits don't have this guard — supersession is naturally
serialised. A racing moment edit will win-last-write.

---

## 10. Profile facts — the Node-driven write

`POST /profile_facts/upsert` exists specifically because the legacy
review UI needs to let users edit Q+A facts on the profile. It is
**the only write surface** for profile facts from Node.

### Cap behaviour

A person can have at most **25 active facts**. At the cap:
- Updates to an existing `fact_key` succeed (no new row count).
- New `fact_key` values return `409`.

Surface the `409` as "you've hit the fact cap — edit an existing one
instead." The UI should probably show the count.

### `fact_key` is free-form

Snake_case slug, ≤ 64 chars. The seven seed slugs (`profession`,
`birthplace`, `residence`, `faith`, `family_role`, `era`,
`personality_essence`) are display defaults — not a registry. Node
can let users invent new slugs.

### Idempotency

Sending the same `(person_id, fact_key, answer_text)` twice is a
natural no-op — the second call returns the existing row id. No
`Idempotency-Key` header needed.

---

## 11. Identity merges — Node-surfaced review + auto-merge

Duplicate-entity handling is now three layers (agent invariant #17):

- **Prevention (automatic, internal).** Extraction reuses an existing
  same-kind entity on a name match instead of creating a duplicate, and
  the extraction LLM reuses known entities via a catalog. **Node/UI need
  do nothing for this** — it just means fewer duplicate entities and far
  fewer review items. Most of the old "review queue" volume disappears.
- **Auto-merge (high confidence).** When the verifier is near-certain two
  entities are the same, the agent merges them **silently** and records a
  notification + an undo snapshot. The user is told after the fact and can
  reverse it.
- **Ask (medium / unsure).** Lower-confidence cases still become `pending`
  review items, exactly as before.

So the old statement "it never merges on its own" is **no longer true**:
high-confidence merges happen automatically. Everything stays reversible.

### Status values Node may now see on `identity_merge_suggestions`

`pending` (ask), `approved`, `rejected`, **`auto_merged`** (silent merge,
reversible), **`unmerged`** (a prior merge the user reversed). New columns:
`confidence`, `acknowledged`, `notification_text`, `undo_snapshot`,
`auto_merged_at`, `unmerged_at` (migration 0024 — one Postgres, applied by
the agent's migrate step; Node only needs to tolerate the new
columns/statuses). Existing `status='pending'` review queries are
unaffected.

### Recommended Node flow

1. **Trigger the reconcile.** Call `POST /identity_merges/scan` for active
   legacies — e.g. once at session-wrap, or a low-frequency cron. Cheap by
   default: candidates are name/alias-gated, so it usually makes zero LLM
   calls. **If Node never calls it, auto-merge never fires** (prevention
   still works); the manual review queue is the only path.
2. **Review pane (unchanged).** Read pending items via
   `GET /identity_merges/suggestions?person_id=...`; render with both
   entity names + the now LLM-authored `reason`; on click →
   `POST /identity_merges/suggestions/{id}/approve|reject`.
3. **Auto-merge toast (new).** Poll
   `GET /identity_merges/auto_merged?person_id=...` for unacknowledged
   silent merges; render a toast using `notification_text` with an "Undo"
   action. Dismiss → `POST /identity_merges/{id}/acknowledge`.
4. **Undo (new).** "Undo" → `POST /identity_merges/{id}/unmerge`. The
   survivor stays intact; the merged-away entity is resurrected as a fresh
   standalone entity with its edges moved back. Works for both auto-merges
   and user-approved merges.

Approval and auto-merge mutate the graph atomically (repoint edges, mark
source `merged`, queue survivor re-embedding) and capture an undo snapshot.
Treat each response as the final state; reverse via `/unmerge`, not a
Node-side rollback.

---

## 11b. Ground-truth taps — the third tap kind (agent invariant #26)

The agent now captures stable subject facts (region, birth decade,
attire, physical features) via the existing tap-card surface, plus a
"when did this happen?" anchor card. What changes for Node:

- **Tap shape.** `metadata.taps[]` entries gained `kind`
  (`coverage | ground_truth | segment_anchor`) and `field` (registry
  key, null for coverage). `question_id` is **null** for the two new
  kinds. Render all three with the same card (question + 4 chips +
  free text + Skip); do **not** post `question_decision` for
  null-`question_id` taps.
- **Answer channel.** When the user taps/types/skips on a
  `ground_truth` or `segment_anchor` card, send the result on the next
  `POST /turn` (or `/turn/stream`) as the optional
  `ground_truth_answer` field:
  `{kind, field, option_label?, free_text?, skipped}`. Do not echo the
  answer into `message` — the sidecar is the only channel, which is
  what keeps demographic Q&A out of the transcript and extraction.
  An answer with no pending tap is silently ignored (replay-safe).
- **Onboarding.** `GET /api/v1/onboarding/archetype-questions` now
  appends two questions (`gt_region`, `gt_birth_era`) to every set;
  the answers array on `archetype-answers` must include them
  (3-12 answers accepted). No other onboarding changes.
- **Reading it.** `persons.ground_truth` JSONB is readable directly
  (one key per field, `{value, provenance, confidence, updated_at}`).
  Read-only for Node — a `POST /ground_truth/upsert` user-edit surface
  is a v2 hook (`provenance='user_edit'` is reserved for it).
- **Artifacts.** Nothing auto-regenerates when ground truth lands. The
  agent injects ground truth into prompt composition at compose time,
  so a manual portrait/scene **regenerate** after a few sessions is
  the recovery path for the wrong-face / wrong-era artifacts.

---

## 12. Local dev / staging / prod

| Environment | Agent base URL | `SERVICE_TOKEN_AUTH_DISABLED` | Notes |
|---|---|---|---|
| Local dev | `http://localhost:8000` | `1` (optional) | Run `uvicorn flashback.http.app:create_app --factory --host 0.0.0.0 --port 8000`. See [`docs/local-dev.md`](docs/local-dev.md). |
| Staging | TBD | `0` | Real tokens, real LLMs, separate AWS account. |
| Production | TBD | `0` | Same — different AWS account. |

For Node-side local dev against the agent, point `AGENT_BASE_URL` at
`http://localhost:8000` and either set the same token both sides or
flip auth off. The frontend still talks only to Node — never expose
`localhost:8000` (the agent) to the browser even in dev.

---

## 13. What changes on the Node side as part of this integration

A non-exhaustive checklist for the Node engineer wiring this in:

- [ ] **Resolve the onboarding mechanism (§4)** — agent endpoint vs
      Node-side `persons` write. This blocks shipping; raise it
      first.
- [ ] Build the onboarding UX: name, relationship, optional photo.
      Wire it to whichever mechanism §4 settles on. Push the
      `artifact_generation` message for the new person's portrait
      (or rely on the agent to do so if option (a)).
- [ ] Add a typed HTTP client for the agent service. Stub from
      `API.md`. One file per endpoint group is fine.
- [ ] Plumb `SERVICE_TOKEN` and `ADMIN_SERVICE_TOKEN` through your
      secret manager + config layer. Inject them on every request.
- [ ] Generate `Idempotency-Key` (UUIDv4) per logical operation for
      the four endpoints listed in §3.
- [ ] Wire `/session/start` and `/turn` into your conversation
      surface. Make sure `session_id` is stable for the lifetime of
      one conversation.
- [ ] Add an inactivity timer / explicit-close hook that calls
      `/session/wrap`. Treat it as required.
- [ ] Make sure your DynamoDB transcript log records the same
      `session_id` you send the agent — that's the join key for any
      cross-system debugging.
- [ ] Add the `artifact_generation` SQS consumer (§7). Wire it to
      your existing image/video generation pipeline. Update the URL
      columns. Set up a DLQ.
- [ ] Add the legacy review UI's edit surfaces and route them to
      `/nodes/.../edit` (moments + entities) and
      `/profile_facts/upsert`. Render placeholders for async-pending
      artifacts and embeddings (§8.1, §8.2).
- [ ] Add the identity merge review pane. Wire it to the
      `/identity_merges/*` endpoints (§11).
- [ ] Add monitoring: agent 5xx rate, p95 latency on `/turn`,
      `artifact_generation` DLQ depth, embedding-lag gauge if you
      care.

---

## 14. Where to ask

When in doubt about whether a thing belongs on Node's side or the
agent's, default to:

- **Reads** of the canonical graph → Node, direct Postgres.
- **Writes** to the canonical graph → agent endpoint (existing or
  new — open an issue here).
- **Reads/writes** of DynamoDB transcripts, S3 artifacts, user/auth
  tables → Node, never the agent.
- **The conversation itself** → agent.

The 17 invariants in `CLAUDE.md` §4 are the formal version of all of
the above. If a proposed integration step would violate one, that's a
flag — surface it before shipping.

---

## Tribute CRM (2026-07-14)

Tribute campaigns / relationship profiles / visual themes are agent-owned
Postgres config. Node builds the CRM screens and proxies every write to
the agent admin API (`/admin/tribute_config/*` etc.) behind the existing
dashboard-admin gate, passing the admin identity as `X-Admin-User`; Node
never writes these tables directly. One runtime change: forward the
campaign slug on `POST /themes/{id}/unlock_prepare` (body field). The full
work order incl. endpoint shapes and the frontend screen contract:
`docs/TRIBUTE_CRM_NODE_PROMPT.md`.
