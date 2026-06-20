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

The SSE streaming variants (`POST /turn/stream`, `POST /session/start/stream`)
**do not support `Idempotency-Key`**. Partial assistant text is committed
to working memory on mid-stream failure, so transcript continuity is
preserved across retries by simply letting the next user turn continue
the conversation.

### Request size limit
Bodies above `MAX_REQUEST_BODY_BYTES` (configured) are rejected with
`413 Payload Too Large`.

### Rate limiting
`POST /turn` and `POST /turn/stream` share the same per-session rate
limit (`turn_rate_limit_per_minute` config). Exceeding it returns
`429 Too Many Requests`. The check runs before either endpoint
streams.

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
| `POST` | `/session/start/stream` | Same as above, streamed as SSE |
| `POST` | `/turn` | One user message → one assistant reply |
| `POST` | `/turn/stream` | Same as above, streamed as SSE |
| `POST` | `/session/wrap` | Force-close the open segment, run post-session sequencing |
| `POST` | `/profile_facts/upsert` | Node-driven edit of one profile fact |
| `GET` | `/identity_merges/suggestions` | List pending entity merge suggestions |
| `POST` | `/identity_merges/scan` | Manually scan a person for merge candidates |
| `POST` | `/identity_merges/suggestions/{id}/approve` | Apply a user-approved merge |
| `POST` | `/identity_merges/suggestions/{id}/reject` | Mark a suggestion rejected |
| `GET` | `/identity_merges/auto_merged` | Feed of silently auto-merged entities (toast source) |
| `POST` | `/identity_merges/{id}/acknowledge` | Dismiss an auto-merge notification |
| `POST` | `/identity_merges/{id}/unmerge` | Reverse an auto/approved merge (resurrect as new entity) |
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
display name, relationship, subject gender, contributor name, and
contributor gender. DOB / DOD are not accepted; `persons` is
intentionally status-agnostic.

**Request**
```json
{
  "name": "string",
  "relationship": "string",
  "contributor_display_name": "string",
  "gender": "he | she | they (optional)",
  "contributor_gender": "he | she | they (optional)",
  "reference_s3_key": "string (optional)"
}
```

`gender` is the **subject's** pronoun form; `contributor_gender` is the
**contributor's**. Both feed artifact generation so the figures in moment
scenes (the subject, and the contributor when a memory includes them —
"my father and I on a bike") render with the correct gender instead of
defaulting. `they` / omitted leaves the figure gender-neutral.

**Response 200**
```json
{
  "person_id": "uuid",
  "name": "string",
  "relationship": "string",
  "gender": "he | she | they | null",
  "contributor_gender": "he | she | they | null",
  "phase": "starter",
  "created_at": "iso-8601"
}
```

### `GET /api/v1/onboarding/archetype-questions`

Return tappable questions tailored to `persons.relationship` (4-5
relationship questions), plus the two ground-truth questions appended
to every set: `gt_region` ("Where did most of their life happen?") and
`gt_birth_era` ("Roughly when were they born?") — CLAUDE.md invariant
26. Both are skippable.

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
`skipped`. The answers array must cover every question returned by
`archetype-questions` exactly once — including `gt_region` and
`gt_birth_era` (3-8 answers accepted). Ground-truth answers are written
to `persons.ground_truth` with `provenance='onboarding'`; they do not
seed entities or coverage.

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
    "taps": [],
    "question_chips": {
      "question_id": "uuid",
      "actions": ["skip", "suppress", "defer"]
    }
  }
}
```

`selected_question_id` is the producer-bank question the opener was
asked to weave into its prose. In **starter phase**, the agent seeds a
question from the per-person bank via `select_starter_question` so the
opener carries an anchor question with chips. In **steady phase**, no
question is seeded at session start (the first `/turn` does the
selection) — `selected_question_id` is `null` and so is
`question_chips`.

`metadata.taps` is reserved for the coverage-tap surface and is always
an empty list on `/session/start`.

`metadata.question_chips` follows the same rule as on `/turn` — present
only when the seeded question's source is in the producer-bank set.
Coverage-tap and archetype questions never produce chips. UI renders
the listed actions beneath the opener and POSTs the chosen action back
in the next `/turn`'s `question_decision`.

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
  "message": "string (1..8000 chars)",
  "question_decision": {
    "question_id": "uuid",
    "action": "skip | suppress | defer"
  },
  "ground_truth_answer": {
    "kind": "ground_truth | segment_anchor",
    "field": "string | null",
    "option_label": "string | null",
    "free_text": "string | null",
    "skipped": false
  }
}
```

`question_decision` is **optional**. When present, the agent records the
decision in the `question_decisions` table before the turn pipeline
runs, so the same call's selector excludes the decided question. See
CLAUDE.md invariant 23.

`ground_truth_answer` is **optional**. It carries the user's tap on a
`ground_truth` / `segment_anchor` card from a prior turn (CLAUDE.md
invariant 26). The agent persists it before the pipeline runs:
person-field answers write to `persons.ground_truth`
(`provenance='tap'`); segment-anchor answers stash in Working Memory
and ride the extraction payload at the next segment boundary;
`skipped: true` suppresses that field for the rest of the session. The
answer is ignored (not an error) when no GT tap is pending — safe
against UI replays. The conversation `message` should NOT duplicate the
answer text; the sidecar is the only channel.

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
        "question_id": "uuid | null",
        "text": "string",
        "dimension": "era | relation | place | voice | sensory | ''",
        "options": ["string"],
        "kind": "coverage | ground_truth | segment_anchor",
        "field": "string | null"
      }
    ],
    "question_chips": {
      "question_id": "uuid",
      "actions": ["skip", "suppress", "defer"]
    }
  }
}
```

`segment_boundary` is `true` on the turn at which the Segment Detector
decided to close a segment and push it onto the extraction queue.
`metadata.taps` is always present. v1 emits at most one coverage-gap tap
on eligible `switch` or `clarify` turns, and at most one ground-truth /
segment-anchor tap per turn on `story` / `deepen` turns (session-capped,
with a 2-user-turn cooldown between tap cards); otherwise it
is `[]`.

Tap `kind` distinguishes the three surfaces. `coverage` taps carry a
real `question_id`; `ground_truth` and `segment_anchor` taps carry
`question_id: null` and (for `ground_truth`) the registry key in
`field`. All three render with the same card UI. Answers to
`ground_truth` / `segment_anchor` taps return via the next `/turn`'s
`ground_truth_answer` sidecar — never as the chat message — and the UI
must not post `question_decision` for null-`question_id` taps.

`metadata.question_chips` is set when the agent inlines a producer-bank
question (sources `dropped_reference`, `underdeveloped_entity`,
`thread_deepen`, `life_period_gap`, `universal_dimension`) in this
reply. The UI renders the listed actions as chips beneath the message;
on click, the next `/turn` request carries the chosen action in
`question_decision`. Absent (or `null`) on turns where no producer-bank
question is surfaced — e.g. when a coverage tap fires instead.

**Errors**
- `409` — no working memory for `session_id` (did `/session/start` succeed?)
- `429` — per-session rate limit
- `503` — LLM / phase gate / dependency error

---

### `POST /turn/stream`

Streaming twin of `POST /turn`. Same request shape, same pre-stream
checks (working memory existence, rate limit, optional
`question_decision` persistence), and same orchestrator pipeline —
the only difference is transport. The response is `text/event-stream`,
status `200`, with named events in this order:

1. exactly one `meta` event (pre-LLM metadata),
2. zero or more `text_delta` events (assistant text chunks),
3. exactly one terminal event — either `done` (success) or `error`
   (failure). No further events follow the terminal event.

`Idempotency-Key` is **not** supported (see §1 idempotency note).

**Request** — identical to `POST /turn`:

```json
{
  "session_id": "uuid",
  "person_id": "uuid",
  "role_id": "uuid",
  "message": "string (1..8000 chars)",
  "question_decision": {
    "question_id": "uuid",
    "action": "skip | suppress | defer"
  }
}
```

**Headers**
- `X-Service-Token` *(required)*
- `Accept: text/event-stream` *(recommended)*

**Response headers**
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no` (asks nginx-style proxies not to buffer)

**Event payloads**

```
event: meta
data: { ...meta payload... }

event: text_delta
data: { "text": "..." }

event: text_delta
data: { "text": "..." }

...

event: done
data: { ...done payload... }
```

`meta` payload:
```json
{
  "intent": "story | switch | recall | clarify | deepen | pivot | null",
  "emotional_temperature": "low | medium | high | null",
  "taps": [
    {
      "question_id": "uuid",
      "text": "string",
      "dimension": "era | relation | place | voice | sensory",
      "options": ["chip 1", "chip 2", "chip 3", "chip 4"]
    }
  ],
  "question_chips": {
    "question_id": "uuid",
    "actions": ["skip", "suppress", "defer"]
  }
}
```

`text_delta` payload — `{ "text": "..." }`. Append in arrival order to
build the streamed reply. Chunks are not pre-trimmed; the agent strips
whitespace only on the assembled `reply` in `done`.

`done` payload — terminal on success:
```json
{
  "reply": "full assistant reply (whitespace-stripped)",
  "segment_boundary": false
}
```

`error` payload — terminal on failure (replaces `done`):
```json
{
  "code": "LLMTimeout | LLMError | LLMMalformedResponse | <ExceptionClass>",
  "message": "human-readable",
  "partial_text": "text streamed before the failure (may be empty)"
}
```

When `error` fires with non-empty `partial_text`, the agent has already
appended the partial reply to working memory so the transcript stays
coherent — the next `/turn` or `/turn/stream` call sees it. Callers
should **not** retry the failed turn.

**Pre-stream errors** — these surface as ordinary HTTP error responses
(no SSE body), the same way `POST /turn` does:

- `409` — no working memory for `session_id`
- `429` — per-session rate limit
- `401` — bad service token

Once the SSE body has started, any failure is reported as a terminal
`error` event with status `200`.

---

### `POST /session/start/stream`

Streaming twin of `POST /session/start`. Same request shape, same
pipeline (load person → apply theme unlock → continuity context →
select starter question → opener). Response is SSE with the same
event types as `/turn/stream`.

`Idempotency-Key` is not applicable (`/session/start` doesn't accept
it either).

**Request** — identical to `POST /session/start`.

**Response headers** — identical to `/turn/stream`.

**Event payloads**

`meta` payload (pre-LLM metadata, available before the opener streams):
```json
{
  "phase": "starter | steady",
  "selected_question_id": "uuid | null",
  "taps": [],
  "question_chips": {
    "question_id": "uuid",
    "actions": ["skip", "suppress", "defer"]
  }
}
```

`taps` is always `[]` on `/session/start/stream` — coverage taps only
fire on `/turn`.

`text_delta` — same as `/turn/stream`.

`done` payload (terminal on success):
```json
{
  "opener": "full opener text",
  "phase": "starter | steady",
  "selected_question_id": "uuid | null",
  "question_chips": {
    "question_id": "uuid",
    "actions": ["skip", "suppress", "defer"]
  }
}
```

`error` payload — same shape as `/turn/stream`.

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

#### Extraction completion — `NOTIFY extraction_complete` + `session_extraction_status`

Moments/entities/traits from a wrapped session land asynchronously, as
the Extraction Worker drains each segment. Rather than polling, the
agent emits a transactional Postgres `NOTIFY` on channel
**`extraction_complete`** once per committed segment. It fires iff the
extraction transaction commits and never on rollback; a **zero-moment**
segment still notifies (so the UI can tell "finished empty" from "still
running"). Postgres is authoritative — the notification is a trigger
only (mirrors the artifact rule in §3 / CLAUDE.md §3).

**Notification payload (channel `extraction_complete`)**
```json
{
  "event": "extraction_complete",
  "session_id": "uuid",
  "person_id": "uuid",
  "segment_message_id": "string",
  "is_final": true,
  "status": "done",
  "moments_written": 3
}
```
`is_final` is `true` only for the wrap-forced tail segment of a session.

**Authoritative read surface — view `session_extraction_status`**

| column              | type        | meaning                                   |
|---------------------|-------------|-------------------------------------------|
| `session_id`        | uuid        | session the segment belonged to           |
| `person_id`         | uuid        | legacy subject                            |
| `segment_message_id`| text        | SQS message id of the extracted segment   |
| `moments_written`   | int         | moments persisted from this segment       |
| `entities_written`  | int         | entities persisted                        |
| `traits_written`    | int         | traits persisted                          |
| `is_final`          | bool        | wrap-forced tail segment                  |
| `status`            | text        | `done`                                    |
| `processed_at`      | timestamptz | commit time (use as the catch-up watermark)|

One row per extracted segment. Node reads this view directly (it is the
contract surface; `processed_extractions` is the agent's internal
table). Node holds a dedicated `LISTEN extraction_complete` connection
(not behind a transaction-mode pooler), and on each notify re-queries
the view for the authoritative set, aggregating per `session_id`. On
listener reconnect, re-query rows newer than the last-seen
`processed_at` to recover any notifications missed while disconnected.
See `NODE_INTEGRATION.md` §8.3.

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
    "source_entity_description": "string | null",
    "target_entity_id": "uuid",
    "target_entity_name": "string",
    "target_entity_description": "string | null",
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

### `GET /identity_merges/auto_merged`

Notification feed of entities the reconcile auto-merged silently (the
`same_identity`+`high` disposition). Node polls this to render an
"we combined these — undo?" toast.

**Query**: `person_id` (UUID, required); `include_acknowledged` (bool,
default `false`).

**Response 200** — array of:
```json
{
  "id": "uuid",
  "person_id": "uuid",
  "source_entity_id": "uuid",
  "target_entity_id": "uuid",
  "survivor_name": "Ishita",
  "notification_text": "You mentioned Ishita again — combined with the earlier one.",
  "confidence": "high",
  "acknowledged": false,
  "auto_merged_at": "2026-06-06T12:00:00Z"
}
```

### `POST /identity_merges/{suggestion_id}/acknowledge`

Dismiss an auto-merge notification (sets `acknowledged=true`). Idempotent.

**Response 200**: `{ "suggestion_id": "uuid", "acknowledged": true }`

**Errors**
- `404` — auto-merged suggestion not found

### `POST /identity_merges/{suggestion_id}/unmerge`

Reverse an auto-merge (or an approved merge). The survivor stays intact;
the merged-away entity is resurrected as a **fresh standalone entity**
with its repointed edges moved back and its deleted duplicate edges
re-created. Pushes a re-embed for the resurrected entity.

**Response 200**:
```json
{
  "suggestion_id": "uuid",
  "person_id": "uuid",
  "survivor_entity_id": "uuid",
  "resurrected_entity_id": "uuid",
  "status": "unmerged"
}
```

**Errors**
- `404` — suggestion not in a reversible state (`auto_merged`/`approved`)

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

## 7b. Tributes — video render

A tribute produces a **video** (shown in-app) + a **PDF** (print). The agent
assembles the story and renders both via the `tribute_render` worker, uploading
to **Node-minted presigned URLs**; Node writes the URL columns on completion.
Full handshake: `NODE_INTEGRATION.md` §7b.

### `POST /tributes/{tribute_id}/generate`

Trigger a tribute video render. Call when the meter is at **100%**
(`tribute_status.percent = 100`).

Request:

```json
{
  "person_id": "<uuid>",
  "artifact_kind": "tribute_video",
  "video_put_url": "<presigned PUT for the MP4 (video/mp4)>",
  "pdf_put_url": "<presigned PUT for the PDF (application/pdf)>",
  "prime_photo_get_url": "<presigned GET for the prime photo, optional>",
  "campaign": "fathers_day_2026",
  "cover_photo_is_prime_years": false
}
```

- `video_put_url` + `pdf_put_url` are **required**; expiry must cover queue
  latency + render (**≥ 24h** recommended). Sign for the content-types shown.
- `prime_photo_get_url` optional — when present the opener becomes a painterly
  portrait of the subject (image-to-image, likeness kept).
- `cover_photo_is_prime_years` — `false` (default) de-ages an older/current
  photo to prime years; `true` skips de-age.

Response `200`:

```json
{
  "job_id": "<uuid>", "tribute_id": "<uuid>", "artifact_kind": "tribute_video",
  "enqueued": true, "percent": 100, "ready": true, "scene_count": 15
}
```

Errors:

- `404` — tribute not found / not owned by `person_id`, or status unavailable.
- `409` — meter below 100% (`detail` carries the current percent).
- `400` — missing `video_put_url` / `pdf_put_url`.
- `410` — `artifact_kind='storybook'`: the tribute storybook is retired; use
  `tribute_video`. (The standalone `/storybooks` feature is separate.)

Generation is **async**: `200` means enqueued. The `tribute_render` worker
renders + PUTs the MP4/PDF, flips `tributes.status` `generating → complete` (or
`failed`), and fires the transactional `tribute_render_complete` NOTIFY. Node
reads the `tribute_status` view (now exposing `pdf_url` + `rendered_at`) and
writes `video_url` / `pdf_url` on that NOTIFY. **Not retry-safe** — a repeat
call re-renders.

### `GET /tributes/{tribute_id}/progress`

Standalone read of the tribute **completion meter** — the same decorated shape
the `/turn` metadata carries as `tribute_progress`, but pollable on its own so
the meter updates without a chat turn. Pure read, no side effects.

Query params:

- `person_id` (**required**, UUID) — owning legacy; scopes the lookup. A tribute
  that doesn't belong to this person `404`s.
- `campaign` (optional, slug) — campaign skin. When set, the `title` and the
  `message` slot's `hint` use the skin copy; otherwise neutral. Pass the same
  slug the UI is themed with (mirrors `/generate`'s `campaign`).

Response `200`:

```json
{
  "percent": 70,
  "ready": false,
  "title": "A Letter to Dad",
  "next": "appearance",
  "slots": [
    {"key": "memories",   "label": "...", "hint": "...", "filled": true,  "count": 3, "target": 3},
    {"key": "message",    "label": "...", "hint": "...", "filled": true,  "count": null, "target": null},
    {"key": "appearance", "label": "...", "hint": "...", "filled": false, "count": null, "target": null},
    {"key": "signature",  "label": "...", "hint": "...", "filled": false, "count": null, "target": null}
  ]
}
```

- `next` is the key of the first unfilled slot (drives the "next — …" steer), or
  `null` when everything is filled. `count`/`target` are populated for the
  `memories` slot only (else `null`). `percent`/`ready` math lives in the
  `tribute_status` SQL view.

Errors:

- `404` — tribute not found / not owned by `person_id`.
- `422` — `person_id` query param missing.

This is the **meter**, not render state. Video/PDF render status (`status`,
`video_url`, `pdf_url`, `rendered_at`) is a separate concern Node reads from the
`tribute_status` view directly (see §7b).

### `GET /tribute-campaigns`

Public campaign list + which campaign is featured today (drives the Father's Day
skin). Returns `{campaigns: [{slug, display_name, featured, is_active,
active_start, active_end}], active_featured_slug}`.

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
