# API.md — Flashback Agent Service ↔ Node.js Backend

This document is the contract between the Node.js Backend (separate
repo) and **this** Python agent service. Node calls us; we never call
Node.

> **Scope.** Node owns reads of the canonical graph for the legacy
> review UI — it has direct Postgres access (read-only) and queries the
> `active_*` views itself. The agent service exposes **writes,
> conversation, and review-side mutations only**. There are deliberately
> no `GET /moments`, `GET /entities`, `GET /threads` etc. endpoints.

---

## 1. Conventions

### Base URL
Set per environment. Service listens on the port `uvicorn` is started
with (default `8000`). All paths below are relative to that base URL.

### Authentication
Every endpoint **except `/health`** requires a service-to-service token.

| Header | Required | Notes |
|---|---|---|
| `X-Service-Token` | yes | Shared secret, validated with `secrets.compare_digest`. |
| `X-Admin-Service-Token` | only for `/admin/*` | Separate token; standard `X-Service-Token` is also required. |

`401 Unauthorized` on missing or wrong token. There is **no per-user
auth** in this service — Node is the auth boundary. The token confirms
"the caller is a trusted internal service," not "the end-user is X."

### Content type
All request and response bodies are `application/json`. UUIDs are
strings in canonical form.

### Idempotency
Mutating endpoints accept an optional `Idempotency-Key` header (≤ 200
chars). When present, the response body is cached for 24h scoped to
the operation, so a retry of a previously-completed call returns the
exact prior response without re-running the operation.

If a second request with the same key arrives **while** the first one
is still in flight, the second receives `409 Conflict` (`"request with
this idempotency key is already in progress"`).

Endpoints that support it:
- `POST /turn`
- `POST /session/wrap`
- `POST /identity_merges/suggestions/{id}/approve`
- `POST /nodes/{node_type}/{node_id}/edit`

For everything else, omit the header.

### Request size limit
Bodies above `MAX_REQUEST_BODY_BYTES` (configured) are rejected with
`413 Payload Too Large`.

### Rate limiting
`POST /turn` enforces a per-session rate limit (`turn_rate_limit_per_minute`
config). Exceeding it returns `429 Too Many Requests`.

### Error envelope
Domain errors use:

```json
{ "detail": "human-readable message" }
```

LLM / phase-gate / unexpected internal errors use:

```json
{ "error": "service_unavailable", "detail": "..." }
```

Common status codes:

| Code | Meaning in this service |
|---|---|
| `400` | malformed body, unknown fact_key shape, invalid session id, oversized idempotency key |
| `401` | missing or invalid service token |
| `404` | person / suggestion / node not found |
| `409` | working memory missing for `session_id`, idempotency-key in flight, fact-cap reached, lost-update on entity edit |
| `413` | request body over limit |
| `422` | pydantic validation failure (extra fields forbidden) |
| `429` | per-session turn rate limit exceeded |
| `502` | LLM call failed or returned malformed output |
| `503` | postgres / valkey / sqs degraded; SQS queue env var missing |
| `504` | LLM timeout |

`extra="forbid"` is set on every request schema — unknown fields are
rejected with `422`.

---

## 2. Endpoint catalogue

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + dependency reachability |
| `POST` | `/persons` | Create an agent-owned `persons` row during onboarding |
| `GET` | `/api/v1/onboarding/archetype-questions` | Return relationship-tailored tappable onboarding questions |
| `POST` | `/api/v1/onboarding/archetype-answers` | Persist archetype answers, seed entities/coverage, return first `session_id` |
| `POST` | `/session/start` | Open a session, return the agent's opener |
| `POST` | `/turn` | One user message → one assistant reply |
| `POST` | `/session/wrap` | Force-close the open segment, run post-session sequencing |
| `POST` | `/profile_facts/upsert` | Node-driven edit of one profile fact |
| `GET` | `/identity_merges/suggestions` | List pending entity merge suggestions |
| `POST` | `/identity_merges/scan` | Manually scan a person for merge candidates |
| `POST` | `/identity_merges/suggestions/{id}/approve` | Apply a user-approved merge |
| `POST` | `/identity_merges/suggestions/{id}/reject` | Mark a suggestion rejected |
| `POST` | `/nodes/{node_type}/{node_id}/edit` | Generic edit for moments and entities |
| `POST` | `/admin/reset_phase` | Force a person back to `starter` phase |

---

## 3. Health

### `GET /health`

Liveness + reachability of valkey, postgres, and the four SQS queues.
**No auth header required** — k8s probes call this.

**Response 200 (`status: ok`)**
```json
{
  "status": "ok",
  "checks": {
    "valkey": "ok",
    "postgres": "ok",
    "sqs.extraction": "ok",
    "sqs.trait_synthesizer": "ok",
    "sqs.profile_summary": "ok",
    "sqs.producers_per_session": "ok"
  }
}
```

**Response 503 (`status: degraded`)** — same body shape; failing
dependencies show `"error: <ExceptionClassName>"`.

---

## 4. Onboarding

Node owns the authenticated user flow. In v1 there is one contributor
per legacy, so the agent stores onboarding completion on the
agent-owned `persons` row instead of a Node-owned `person_roles` row.

### `POST /persons`

Create the `persons` row after the contributor supplies the subject's
display name, relationship, subject gender, and contributor name. DOB
/ DOD are not accepted; `persons` is intentionally status-agnostic.

### `GET /api/v1/onboarding/archetype-questions`

Return 2-3 tappable questions tailored to `persons.relationship`.

**Query**
```
person_id=uuid
```

**Response 200**
```json
{
  "person_id": "uuid",
  "relationship": "friend",
  "archetype": "friend",
  "questions": [
    {
      "id": "friend_meet",
      "text": "How did you two first meet?",
      "allow_free_text": true,
      "allow_skip": true,
      "options": [
        { "id": "school", "label": "At school or college" }
      ]
    }
  ]
}
```

The server-side `implies` blocks are deliberately omitted.

**Errors**
- `404` -- person not found
- `409` -- `persons.onboarding_complete = true`
- `503` -- `persons` onboarding columns are unavailable

### `POST /api/v1/onboarding/archetype-answers`

Validate every archetype question, resolve static option implications,
parse free-text answers with the small LLM parser, upsert implied
entities, bump `persons.coverage_state`, store
`persons.archetype_answers`, set
`persons.onboarding_complete = true`, enqueue new entity
embeddings when configured, and return the first session id.

**Request**
```json
{
  "person_id": "uuid",
  "answers": [
    { "question_id": "friend_meet", "option_id": "school" },
    {
      "question_id": "friend_first_impression",
      "option_id": null,
      "free_text": "He was quietly confident"
    },
    { "question_id": "friend_shared_place", "skipped": true }
  ]
}
```

Each answer must choose exactly one of `option_id`, `free_text`, or
`skipped`.

**Response 200**
```json
{ "session_id": "uuid" }
```

Node should use that `session_id` for the immediate
`POST /session/start` call. The endpoint already uses the stored
`persons.archetype_answers` for the first opener; passing the same
array in `session_metadata.archetype_answers` is optional.

**Errors**
- `404` -- person not found
- `409` -- onboarding already complete
- `422` -- incomplete, duplicate, or invalid answers
- `502` / `504` -- free-text parser failure or timeout

---

## 5. Conversation lifecycle

### `POST /session/start`

Open a session for a contributor, hydrate working memory, run phase
gate + question selection, return the agent's opener.

**Request**
```json
{
  "session_id": "uuid",
  "person_id": "uuid",
  "role_id": "uuid",
  "contributor_display_name": "string (optional, recommended)",
  "session_metadata": {
    "prior_session_summary": "string (optional)",
    "archetype_answers": "array (optional, first session)"
  }
}
```

`contributor_display_name` is the contributor's display name (e.g.
`"Sarah"`). Recommended on every new session. When provided, it's
stored in working memory and made available to **archive-side text
generation** — entity descriptions, moment narratives, thread
summaries, profile summary, profile facts — so attribution can read
naturally ("Sarah recalls his laugh", "John, Sarah's father, was a
carpenter").

The opener may use the contributor name as context for the relationship,
but the agent should not use it as a repeated salutation. `/turn`
responses stay relationship-centered rather than name-heavy.
When omitted or null, archive-side text falls back to neutral
attribution ("the contributor", or omitted). Not persisted across
sessions today — pass it on every `/session/start`.

`session_metadata` is a free-form dict. The keys the agent reads today
are:

- `prior_session_summary`, which seeds the read-only
  `prior_session_summary` field in working memory (consumed only by the
  Response Generator — see invariant #15).
- `archetype_answers`, the stored onboarding answers for the person. The
  first-turn opener renders these naturally and anchors on the most
  concrete detail without re-asking it.

**Response 200**
```json
{
  "session_id": "uuid",
  "opener": "string",
  "metadata": {
    "phase": "starter | steady",
    "selected_question_id": "uuid | null",
    "taps": []
  }
}
```

`selected_question_id` is retained for compatibility and is always null
for v1 session openers. `metadata.taps` is reserved for the tap-chip
shape and is always an empty list on `/session/start`.

**Errors**
- `404` — `person_id` not found
- `503` — phase gate or LLM call failed

---

### `POST /turn`

One user message in, one assistant reply out. Idempotent on
`Idempotency-Key`.

**Request**
```json
{
  "session_id": "uuid",
  "person_id": "uuid",
  "role_id": "uuid",
  "message": "string (1..8000 chars)"
}
```

**Headers**
- `Idempotency-Key` *(optional)*

**Response 200**
```json
{
  "reply": "string",
  "metadata": {
    "intent": "string | null",
    "emotional_temperature": "low | medium | high | null",
    "segment_boundary": false,
    "taps": [
      {
        "question_id": "uuid",
        "text": "string",
        "dimension": "era | relation | place | voice | sensory"
      }
    ]
  }
}
```

`segment_boundary` is `true` on the turn at which the Segment Detector
decided to close a segment and push it onto the extraction queue.
`metadata.taps` is always present. v1 emits at most one coverage-gap tap
on eligible `switch` or `clarify` turns; otherwise it is `[]`.

**Errors**
- `409` — no working memory for `session_id` (did `/session/start` succeed?)
- `429` — per-session rate limit
- `503` — LLM / phase gate / dependency error

---

### `POST /session/wrap`

Force-close the open segment, generate the session summary, fan out to
post-session workers (extraction → traits → profile summary → P2/P3/P5).
Idempotent on `Idempotency-Key`.

**Request**
```json
{ "session_id": "uuid", "person_id": "uuid" }
```

**Headers**
- `Idempotency-Key` *(optional)*

**Response 200**
```json
{
  "session_summary": "string",
  "metadata": { "segments_extracted_count": 0 }
}
```

`segments_extracted_count` is the number of segments pushed onto the
extraction queue by this wrap (typically 0 or 1 — the unflushed tail).

**Errors**
- `409` — no working memory for `session_id` (already wrapped, or
  never started)

---

## 5. Profile facts

### `POST /profile_facts/upsert`

Node-driven write surface for the open-ended Q+A facts displayed on
the legacy profile. Supersedes the prior active row (if any) and
inserts a new row with `source = "user_edit"`. Pushes an `embedding`
queue job for the new row.

If the new `answer_text` is identical to the existing active row, no
write is performed and the existing row id is returned (idempotent
no-op).

If no row exists for `(person_id, fact_key)` and the person already has
**25 active facts**, the request is rejected with `409`.

**Request**
```json
{
  "person_id": "uuid",
  "fact_key": "snake_case_slug",
  "answer_text": "string (1..300 chars)",
  "question_text": "string (1..300 chars, optional)"
}
```

`fact_key` is free-form snake_case. The seven seed slugs (`profession`,
`birthplace`, `residence`, `faith`, `family_role`, `era`,
`personality_essence`) auto-resolve their canonical question text if
`question_text` is omitted. For non-seed slugs, omit and the agent
falls back to a generic `"What about {name}'s <pretty key>?"` phrasing.

**Response 200**
```json
{
  "fact_id": "uuid",
  "person_id": "uuid",
  "fact_key": "string",
  "superseded_id": "uuid | null",
  "cap_reached": false
}
```

**Errors**
- `409` — person at the 25-active-fact cap (and this is a new key)
- `503` — `EMBEDDING_QUEUE_URL` not configured

---

## 6. Identity merges

Detection is automatic (extraction may write `pending` rows), but
**mutation always requires user approval**. Surface these via Node/UI
as an out-of-band review pane, not inside the memorial conversation.

### `GET /identity_merges/suggestions`

**Query params**
- `person_id` *(uuid, required)*
- `status_filter` *(optional, default `pending`)* — one of `pending`,
  `approved`, `rejected`

**Response 200** — `IdentityMergeSuggestion[]`:
```json
[
  {
    "id": "uuid",
    "person_id": "uuid",
    "source_entity_id": "uuid",
    "source_entity_name": "string",
    "target_entity_id": "uuid",
    "target_entity_name": "string",
    "proposed_alias": "string | null",
    "reason": "string",
    "source": "string",
    "status": "pending | approved | rejected",
    "created_at": "iso-8601"
  }
]
```

---

### `POST /identity_merges/scan`

Run the deterministic + small-LLM verifier scan over a person's
entities, creating `pending` suggestions for plausible duplicates. Does
not mutate `entities` directly.

**Request**
```json
{ "person_id": "uuid", "limit": 20 }
```
`limit` is the max number of candidate pairs to consider (1..100,
default 20).

**Response 200**
```json
{
  "person_id": "uuid",
  "candidates_considered": 0,
  "verifier_calls": 0,
  "suggestions_created": 0,
  "suggestion_ids": ["uuid"]
}
```

---

### `POST /identity_merges/suggestions/{suggestion_id}/approve`

Apply the merge: repoint edges from `source_entity_id` →
`target_entity_id`, mark the source `merged`, update the survivor's
aliases / description, push a re-embedding job for the survivor. All
in one transaction. Idempotent on `Idempotency-Key`.

**Headers**
- `Idempotency-Key` *(optional)*

**Response 200**
```json
{
  "suggestion_id": "uuid",
  "person_id": "uuid",
  "source_entity_id": "uuid",
  "target_entity_id": "uuid",
  "status": "approved"
}
```

**Errors**
- `404` — pending suggestion not found
- `503` — `EMBEDDING_QUEUE_URL` not configured

---

### `POST /identity_merges/suggestions/{suggestion_id}/reject`

Mark a pending suggestion `rejected` without changing graph entities.

**Response 200** — same shape as approve, with `"status": "rejected"`.

**Errors**
- `404` — pending suggestion not found

---

## 7. Node edits — `POST /nodes/{node_type}/{node_id}/edit`

The single user-edit write surface for the canonical graph. The
contributor edits the primary text field of a node from the legacy
review UI; Node forwards the revised free text here. The agent re-runs
the relevant extraction-style LLM, applies the per-type strategy, and
fans out queue jobs.

The endpoint is registry-driven. v1 supports two `node_type`s:

| `node_type` | Edits | Mutation strategy | Edges | Re-embeds | Artifact |
|---|---|---|---|---|---|
| `moment` | `narrative` (and LLM-derived structured fields) | `supersede` (insert new + flip old to `superseded`, repoint inbound edges, drop+rebuild outbound `involves`/`happened_at`) | re-extract from new narrative | yes (`narrative_embedding`) | new `video` job |
| `entity` | `description` (and LLM-derived attributes) | `in_place` (UPDATE columns, clear embedding) | unchanged | yes (`description_embedding`) | new `image` job |

The LLM is **not** allowed to change immutable fields. For moments:
`id`, `person_id`, `status`, `superseded_by`, embedding columns, URL
columns, `created_at`. For entities, additionally: `kind`, `name`
(rename = create a new entity, not edit).

**Path params**
- `node_type` — `moment` | `entity`
- `node_id` — uuid

**Request**
```json
{
  "person_id": "uuid",
  "free_text": "string (1..8000 chars)"
}
```

`person_id` is required so the engine can verify the row belongs to
that legacy and refuse cross-legacy edits.

**Headers**
- `Idempotency-Key` *(optional)*

**Response 200**
```json
{
  "node_type": "moment | entity",
  "node_id": "uuid",
  "superseded_id": "uuid | null",
  "new_entity_ids": ["uuid"],
  "edges_added": 0,
  "edges_removed": 0,
  "artifact_queued": true,
  "embedding_jobs_pushed": 1
}
```

- For `moment`: `node_id` is the **new** moment row (the post-edit
  one); `superseded_id` is the previous active row that is now
  `status='superseded'`. `new_entity_ids` are any entities created by
  re-extraction.
- For `entity`: `node_id` equals the input `node_id`; `superseded_id`
  is null (in-place update).

**Errors**
- `404` — `person_id` not found, or no active node matches
  `(node_id, person_id)`
- `409` — concurrent in-place update on an entity (lost update;
  refresh and retry)
- `422` — unknown `node_type` (forward-compat guard; pydantic also
  rejects)
- `502` — edit-LLM call failed or returned output that fails
  validation
- `503` — `EMBEDDING_QUEUE_URL` not configured, or `ARTIFACT_QUEUE_URL`
  not configured (every supported `node_type` regenerates artifacts)
- `504` — edit-LLM timeout

> **Out of scope for v1.** Edits to `threads` and `traits` are not yet
> supported. To dismiss a wrong trait, use a DB-side correction; we
> expect to expose `trait` (`status` flip to `superseded`) and `thread`
> in a follow-up by adding registry entries.

---

## 8. Admin

### `POST /admin/reset_phase`

Escape hatch for the sticky Handover Check. Flips a person back to
`starter`, clears `phase_locked_at`, zeroes `coverage_state`. Single
statement; no fan-out.

**Auth** — requires **both** `X-Service-Token` and
`X-Admin-Service-Token`.

**Request**
```json
{ "person_id": "uuid" }
```

**Response 200**
```json
{
  "person_id": "uuid",
  "previous_phase": "starter | steady",
  "previous_locked_at": "iso-8601 | null"
}
```

**Errors**
- `404` — `person_id` not found

---

## 9. What this service does NOT expose

By design — Node reads these directly from Postgres:

- `GET /moments`, `GET /moments/{id}`
- `GET /entities`, `GET /entities/{id}`
- `GET /threads`, `GET /threads/{id}`, `GET /threads/{id}/moments`
- `GET /traits`, `GET /persons/{id}/traits`
- `GET /persons/{id}` (profile / display name / coverage_state)
- `GET /profile_facts?person_id=...`
- `GET /questions/...`
- Any DynamoDB transcript reads (Node-owned)
- Any S3 / artifact URL reads (Node-owned, Node writes the URL columns)

If the UI needs a read endpoint that requires agent-side computation
(not just a SQL query against `active_*`), open a discussion before
adding it here. The default answer is "Node queries Postgres."

---

## 10. Versioning

There is no `/v1/` URL prefix today. Breaking changes to request /
response shapes are coordinated repo-to-repo via PRs that update both
this `API.md` and the Node client at the same time. If the surface
becomes externally consumed, prefix all routes with `/v1/` and bump.
