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
| `POST /turn` | 45s — intent + retrieval + response LLM |
| `POST /session/wrap` | 60s — segment flush + summary LLM |
| `POST /nodes/.../edit` | 45s — edit-LLM call |
| `POST /identity_merges/scan` | 60s — small LLM per candidate |
| All other writes | 15s |

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
   show the returned 2-3 tappable questions. The response does not
   expose server-side `implies` blocks.
4. Call `POST /api/v1/onboarding/archetype-answers` with `person_id`
   and one answer
   per returned question. Each answer chooses exactly one of
   `option_id`, `free_text`, or `skipped`.
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
| Open questions / "ask next" | `active_questions` (filtered by status / answered_by edges) |
| Identity merge review | `identity_merge_suggestions` (the GET endpoint is more convenient) |

### 6.5 Columns Node writes

Strictly limited:

| Table | Columns Node may write |
|---|---|
| `persons` | `image_url`, `thumbnail_url` |
| `moments` | `video_url`, `thumbnail_url` |
| `entities` | `image_url`, `thumbnail_url` |
| `threads` | `image_url`, `thumbnail_url` |

These are the artifact URL columns. Node writes them after the
artifact-generation worker uploads to S3. **Nothing else.**

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

### 8.3 `/session/wrap` returns before extraction completes

Wrap synchronously generates the session summary and pushes the
unflushed segment onto the `extraction` queue. The actual moments,
entities, traits, etc. land in Postgres **later**, after the
Extraction Worker → Trait Synthesizer → Profile Summary chain runs.

**UI implication.** The legacy profile may not show the latest
session's moments for ~30s after wrap. Don't poll the agent — just
re-query Postgres on next page load.

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

## 11. Identity merges — Node-surfaced review

The agent detects probable duplicate entities and writes `pending`
rows to `identity_merge_suggestions`. **It never merges entities on
its own.** The UI must surface a review pane (out-of-band toast,
review queue, etc. — not inside the conversation).

### Recommended Node flow

1. Periodically (or on-demand) call `POST /identity_merges/scan` for
   active legacies — rate-limit this; it's a costly LLM fan-out.
2. Read pending suggestions via `GET /identity_merges/suggestions?person_id=...`.
   (Node could also read the table directly; the GET endpoint exists
   for symmetry and forward-compat.)
3. Render them in a review pane with both entity names + the
   `reason` field.
4. On user click → `POST /identity_merges/suggestions/{id}/approve` or
   `.../reject`.

Approval mutates the graph atomically (repoints edges, marks source
`merged`, queues survivor re-embedding). Treat the response as the
final state; don't roll back on the Node side.

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
