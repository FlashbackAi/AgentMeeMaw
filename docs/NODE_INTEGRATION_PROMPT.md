# Node Integration Prompt — Flashback Artifact Generation v2

**This is the canonical, self-contained brief for the Node.js Backend team
(or any AI assistant implementing it).** Read it end-to-end before
writing code. References to other docs are pointers, not prerequisites.

**Status:** Agent service is on Option A — Postgres-authoritative, SQS
as trigger-only, uniform shape across all artifact paths (auto /
onboarding / regenerate / edit). Schema migration `0023` deployed in the
agent repo. Node has not yet implemented the consumer.

**Goal:** Node becomes a dumb worker. It receives a trigger from SQS,
reads the composed prompt from Postgres, generates the artifact, writes
the URL back. No prompt logic on Node. No negative-prompt handling. No
preset translation. The agent owns every decision about what the model
generates; Node owns *how* to call the model and where to put the result.

---

## Table of contents

1. Why we did this — rationale
2. Architecture diagram + data flow
3. What changed at a glance (vs. pre-session HEAD)
4. HTTP endpoints Node calls (full request / response shapes)
5. SQS queues Node consumes (full payload shapes)
6. Postgres reads Node performs (the `latest_generation_context` JSONB)
7. Postgres writes Node performs (URL columns only)
8. DynamoDB edit-history table (Node-owned)
9. S3 layout (uploads + results)
10. User-facing UI — what to build
11. Worker pseudocode (both queues)
12. Versioning, rollout, deploy ordering
13. Infrastructure setup (SQS, IAM, DLQ, CloudWatch)
14. Error handling, retries, idempotency
15. Acceptance checklist
16. Known issues + mitigations
17. Glossary

---

## 1. Why we did this

### The problem before this rollout

The agent pushed SQS messages with the full prompt inline. Node read the
prompt from the message body and called the image model with it. This
worked for first-time auto-generation but had three structural problems
once user edits and presets entered the picture:

- **Composed prompts (base + edits + preset modifier) only lived in the
  SQS message.** Postgres only had the original LLM-emitted base. If a
  message got requeued / replayed late, there was no source of truth to
  fall back on.
- **Node had to know about prompt internals.** Negative prompts, preset
  modifiers, reference-image keys, edit stacking — all of it surfaced in
  the SQS payload, which meant Node code had to parse and forward each
  field. Adding a preset or a new edit dimension required Node deploys.
- **Two contract surfaces had to evolve in lock-step.** Every time the
  agent added a prompt-side feature (new preset, new style modifier,
  new negative term), the SQS contract changed and Node had to ship.

### The fix

- The agent composes the full generation context (prompt + negative +
  mode + reference + preset + source + composed_at) and writes it to a
  new JSONB column `latest_generation_context` on every artifact-bearing
  row, **before** pushing the SQS message.
- The SQS payload is now **trigger-only**: 8 identifier fields, no
  content. Same shape across auto / onboarding / regenerate / edit.
- Node's worker becomes: parse trigger → `SELECT latest_generation_context
  FROM <table> WHERE id = %s` → generate → write URL. Same algorithm for
  every artifact source. Future prompt-side features (new presets, new
  edit dimensions) ship from the agent side alone — Node doesn't change.

### The contract this gives you

> **The agent owns *what* gets generated. Node owns *how* to call the
> model and *where* to put the result.**

If you find yourself writing prompt-string logic on the Node side, stop —
the agent is supposed to be doing that work, and routing it through
Node creates the lock-step problem we just removed.

---

## 2. Architecture diagram + data flow

```
┌────────────┐    ┌──────────────────────────────────────────────┐
│  Frontend  │    │             Agent (Python)                   │
│            │    │                                              │
│  edit UI ──┼───▶│  POST /artifacts/{type}/{id}/edit            │
│            │    │      │                                       │
│  regen ────┼───▶│  POST /artifacts/{type}/{id}/regenerate      │
│            │    │      │                                       │
│  upload ───┼─┐  │  POST /persons/{id}/profile-picture[/edit]   │
└────────────┘ │  │      │                                       │
               │  │      ▼                                       │
               │  │  compose prompt + negative + preset modifier │
               │  │      │                                       │
               │  │      ▼                                       │
               │  │  UPDATE <table>.latest_generation_context    │ ◀─── single source of truth
               │  │      │                                       │
               │  │      ▼                                       │
               │  │  push trigger to SQS  ────────┐              │
               │  └────────────────────────────────┼──────────────┘
               │                                  │
               │   S3 (uploads)                   │
               └──▶ uploads/{user}/{type}/{id}/   │
                   {ts}.{ext}                     │
                                                  ▼
                                    ┌─────────────────────────────────┐
                                    │     Node.js Backend             │
                                    │                                 │
                                    │  SQS worker                     │
                                    │    │                            │
                                    │    ▼                            │
                                    │  SELECT latest_generation_context
                                    │    │                            │
                                    │    ▼                            │
                                    │  image / video model            │
                                    │    │                            │
                                    │    ▼                            │
                                    │  S3 (results)                   │
                                    │    │                            │
                                    │    ▼                            │
                                    │  UPDATE <table>.image_url       │
                                    │                                 │
                                    │  Dynamo (edit history)          │
                                    │    flashback_artifact_edits     │
                                    └─────────────────────────────────┘
```

### End-to-end edit flow (the most complex case)

1. **User clicks "Edit" on a moment card** in the frontend.
2. Frontend hits Node: `GET /artifact-edit-history/{record_type}/{record_id}`.
3. **Node reads Dynamo:** `flashback_artifact_edits[(record_type, record_id)]`. Returns `{prior_instructions, last_reference_s3_key, last_preset}` for the modal to pre-populate.
4. **User types "more snow on the ground"**, picks the `golden_hour` preset, optionally uploads a reference photo.
5. If a photo was uploaded, Frontend → Node → **S3 upload** under `uploads/{user_id}/{record_type}/{record_id}/{ts}.{ext}`. Node returns the key to the frontend.
6. Frontend hits Node submit endpoint, Node forwards to agent: `POST /artifacts/moment/{record_id}/edit` with `{person_id, instructions, prior_instructions, preset, reference_s3_key}`.
7. **Agent composes the prompt** (base scene description + prior edits + new edit + preset modifier), writes the full context to `moments.latest_generation_context`, pushes a trigger to `flashback_agent-artifact-generation`.
8. Agent returns 200 to Node.
9. **Node appends the new instruction to Dynamo** `prior_instructions`.
10. Node returns 200 to Frontend.
11. **Async:** Node's SQS worker drains the trigger:
    - Parses `record_type=moment`, `record_id`, `composed_at`.
    - `SELECT latest_generation_context FROM moments WHERE id = %s`.
    - Stale check: if `context.composed_at > message.composed_at`, skip + delete.
    - Generates the image with `context.prompt`, `context.negative_prompt`, optional reference image from S3.
    - Uploads result to S3 under your existing key scheme.
    - Writes `image_url` / `video_url` / `thumbnail_url` back to Postgres.
    - Deletes the SQS message.
12. Frontend polls / re-fetches the moment and shows the new image.

### Auto-extraction flow (background, no user interaction)

1. Extraction LLM emits new moments + entities for a session.
2. Agent's persistence layer commits rows with `generation_prompt` (LLM-emitted base) AND writes `latest_generation_context` inside the same transaction (with `negative_prompt=null`, `mode='no_reference'`, `preset=null`, `source='auto'`).
3. Agent pushes one trigger per row to `flashback_agent-artifact-generation`.
4. Node's worker drains identically to the edit case — same algorithm.

### Onboarding portrait flow

1. Node calls agent `POST /persons` to create a person.
2. Agent inserts the `persons` row, composes the portrait prompt, writes `persons.latest_generation_context`, pushes a trigger to `flashback_agent-profile-picture`.
3. Node's portrait worker drains, reads context from Postgres, generates, writes `image_url` and `thumbnail_url` on the persons row.

---

## 3. What changed at a glance (vs. pre-session HEAD)

| Surface | Before | After |
|---|---|---|
| SQS payload — artifact queue | `{record_type, record_id, person_id, artifact_kind, generation_prompt}` | `{job_id, record_type, record_id, person_id, artifact_kind, source, composed_at, enqueued_at}` |
| SQS payload — profile-picture queue | Rich payload with `image_prompt`, `negative_prompt`, `model_hints`, `raw_inputs.*`, `mode`, `reference_s3_key` | Same trigger-only shape as above (with `record_type='person'`) |
| Postgres `<table>.generation_prompt` | LLM-emitted base scene description | **Unchanged** — still the immutable base |
| Postgres `<table>.latest_generation_context` | **Did not exist** | **New JSONB column** carrying `{prompt, negative_prompt, mode, reference_s3_key, preset, source, composed_at}` for every artifact-bearing row |
| Where Node reads the prompt | SQS message body | Postgres `latest_generation_context.prompt` |
| HTTP endpoints on agent | `/persons/{id}/profile-picture[/edit]` | Same + new: `GET /artifact-presets`, `POST /artifacts/{type}/{id}/{regenerate,edit}` |
| Style register | Pixar / Studio Ghibli | RDR2 painterly-realism (with anti-deity negative for portraits) |
| Preset support | None | 5 slugs across all four record types |
| Edit stacking | None | `prior_instructions` cumulative history; Node tracks in Dynamo |
| Reference uploads | None | `reference_s3_key` for moments + entities (threads excluded) |

---

## 4. HTTP endpoints Node calls

All endpoints require the service-token header (existing convention):

```
X-Service-Token: <SERVICE_TOKEN>
```

### 4.1 `GET /artifact-presets`

Returns the list of style/mood presets the user can pick. Cache this
(24h TTL is fine — slugs are stable).

**Request:** no body.

**Response 200:**

```json
{
  "presets": [
    {
      "slug": "painterly_cinematic",
      "label": "Painterly cinematic",
      "description": "The default Flashback look — RDR2-style painterly realism with soft cinematic lighting.",
      "is_default": true
    },
    {
      "slug": "golden_hour",
      "label": "Golden hour",
      "description": "Warm late-afternoon light with long soft shadows.",
      "is_default": false
    },
    {
      "slug": "twilight",
      "label": "Twilight",
      "description": "Cool blue-hour light, rich shadows, warm window glow.",
      "is_default": false
    },
    {
      "slug": "storybook",
      "label": "Storybook",
      "description": "Softer painterly brushwork, gentle storybook warmth.",
      "is_default": false
    },
    {
      "slug": "vintage_film",
      "label": "Vintage film",
      "description": "Subtle film grain, faded color, 70s photochrome palette.",
      "is_default": false
    }
  ]
}
```

**Notes:**

- Default first. The `is_default` flag is the source of truth — don't hardcode position.
- Slugs are part of the contract; labels/descriptions can change without notice. Display labels, send slugs.
- Unknown slugs sent back to the agent return 400.

### 4.2 `POST /artifacts/{record_type}/{record_id}/regenerate`

Re-roll the artifact with an optional preset + optional reference image.
Does NOT consume prior edit history.

**Path params:**

- `record_type` ∈ `{moment, entity, thread}` — `person` is **excluded**, use the profile-picture endpoint instead.
- `record_id` — UUID of the record.

**Request body:**

```json
{
  "person_id": "<uuid>",
  "preset": "<slug>" | null,
  "reference_s3_key": "<s3 key>" | null
}
```

| Field | Rule |
|---|---|
| `person_id` | Required. Must match the record's owner. |
| `preset` | Optional. Slug from `/artifact-presets`. `null` → default `painterly_cinematic`. |
| `reference_s3_key` | Optional. **Not allowed for `record_type=thread`** (returns 400). For moments + entities, S3 key of a user-uploaded reference image. |

**Response 200:**

```json
{
  "job_id": "<fresh uuid>",
  "record_type": "moment",
  "record_id": "<uuid>",
  "person_id": "<uuid>",
  "artifact_kind": "video",
  "mode": "with_reference",
  "source": "regenerate",
  "preset": "golden_hour",
  "enqueued": true
}
```

`enqueued: false` means the agent wrote the context to Postgres but the SQS queue URL wasn't configured (local dev). Don't treat as an error.

**Error responses:**

- `400` — bad preset, reference_s3_key on a thread, or invalid record_type.
- `404` — record doesn't exist for this `person_id`, or has no `generation_prompt`.

### 4.3 `POST /artifacts/{record_type}/{record_id}/edit`

Append a new instruction to the cumulative edit history. Agent composes
`base + prior_instructions + instructions + preset modifier`, writes context to
Postgres, pushes trigger.

**Path params:** same as regenerate.

**Request body:**

```json
{
  "person_id": "<uuid>",
  "instructions": "more snow on the ground",
  "prior_instructions": [
    "add a red truck",
    "warmer porch light"
  ],
  "reference_s3_key": "<s3 key>" | null,
  "preset": "<slug>" | null
}
```

| Field | Rule |
|---|---|
| `instructions` | Required. 1-500 chars after trim. The newest edit text. |
| `prior_instructions` | Optional, default `[]`. Cumulative history Node tracks in Dynamo, **oldest first**. Max 50 entries. Blank entries are dropped. |
| `reference_s3_key` | Optional. Same rules as regenerate (threads reject). |
| `preset` | Optional. Same rules as regenerate. |

**Response 200:** same shape as regenerate, with `source: "edit"`.

### 4.4 `POST /persons/{person_id}/profile-picture`

Re-generate the portrait. Same shape as `/artifacts/.../regenerate` but
without `record_type` / `person_id` in the body (path-derived).

**Request body:**

```json
{
  "preset": "<slug>" | null,
  "reference_s3_key": "<s3 key>" | null
}
```

**Response 200:**

```json
{
  "job_id": "<fresh uuid>",
  "person_id": "<uuid>",
  "mode": "with_reference" | "no_reference",
  "source": "regenerate",
  "preset": "<resolved slug>",
  "enqueued": true
}
```

### 4.5 `POST /persons/{person_id}/profile-picture/edit`

Same as `/artifacts/.../edit` but for portraits. `record_type=person` is
implicit.

**Request body:**

```json
{
  "instructions": "give him round glasses",
  "prior_instructions": ["wearing a brown sherwani"],
  "reference_s3_key": "<s3 key>" | null,
  "preset": "<slug>" | null
}
```

**Response 200:** same as 4.4 but with `source: "edit"`.

### 4.6 `POST /persons` (existing, no shape change for this rollout)

Creates a person. Onboarding portrait push happens automatically:
agent composes default portrait prompt, writes
`persons.latest_generation_context`, pushes trigger with
`source="onboarding"`. Node side: no change here, just be aware the
portrait worker will receive an onboarding trigger.

---

## 5. SQS queues Node consumes

### 5.1 `flashback_agent-artifact-generation` (moments / entities / threads)

**Queue name:** existing — `ARTIFACT_QUEUE_URL`.
**Type:** Standard SQS.

**Payload (trigger-only):**

```json
{
  "job_id":        "<uuid>",
  "record_type":   "moment" | "entity" | "thread",
  "record_id":     "<uuid>",
  "person_id":     "<uuid>",
  "artifact_kind": "image" | "video",
  "source":        "auto" | "regenerate" | "edit",
  "composed_at":   "<ISO-8601 UTC>",
  "enqueued_at":   "<ISO-8601 UTC>"
}
```

| Field | Notes |
|---|---|
| `job_id` | Fresh UUID per push. Use for idempotency / dedup at the consumer if needed. |
| `record_type` | `moment` → `artifact_kind=video`. `entity` → `image`. `thread` → `image`. |
| `record_id` | The row's PK in `<table>` (where table = `record_type + "s"`: `moments`, `entities`, `threads`). |
| `person_id` | The legacy owner. Use for partitioning / multi-tenancy if your worker needs it. |
| `artifact_kind` | Derived from `record_type`. Tells you which model + S3 layout + URL column to write. |
| `source` | `auto` — pushed by the extraction worker / thread detector on first-time creation. `regenerate` — user picked a preset / uploaded a reference. `edit` — user typed an instruction. Use for analytics; not used for routing. |
| `composed_at` | Matches the row's `latest_generation_context.composed_at` at push time. **Critical for stale-trigger detection.** |
| `enqueued_at` | When the agent pushed the message. For latency tracking. |

### 5.2 `flashback_agent-profile-picture` (portraits)

**Queue URL:** `https://sqs.ap-south-1.amazonaws.com/768699754860/flashback_agent-profile-picture`
**Type:** Standard SQS.

**Payload (trigger-only — same shape as artifact queue):**

```json
{
  "job_id":        "<uuid>",
  "record_type":   "person",
  "record_id":     "<person_id>",
  "person_id":     "<person_id>",
  "artifact_kind": "image",
  "source":        "onboarding" | "regenerate" | "edit",
  "composed_at":   "<ISO-8601 UTC>",
  "enqueued_at":   "<ISO-8601 UTC>"
}
```

Note: `record_id` and `person_id` are equal for this queue (a portrait
*is* the person record). `record_type` is always `"person"`. `source`
includes `"onboarding"` here, which doesn't apply to the artifact queue.

---

## 6. Postgres reads Node performs

### 6.1 `<table>.latest_generation_context` (JSONB)

Lives on `persons`, `moments`, `entities`, `threads`. Added in migration
0023. Populated by the agent on every artifact push. Read this at job
processing time:

```sql
SELECT latest_generation_context
  FROM moments              -- or entities / threads / persons
 WHERE id = $1
   AND status = 'active'    -- skip superseded rows
```

**Shape:**

```json
{
  "prompt":            "<composed prompt — pass directly to the image / video model>",
  "negative_prompt":   "<negative prompt>" | null,
  "mode":              "no_reference" | "with_reference",
  "reference_s3_key":  "<s3 key>" | null,
  "preset":            "<slug>" | null,
  "source":            "auto" | "onboarding" | "regenerate" | "edit",
  "composed_at":       "<ISO-8601 UTC>"
}
```

| Field | Notes for Node |
|---|---|
| `prompt` | Already composed. Pass directly to the model. Do not mutate. |
| `negative_prompt` | Pass to the model when present. **When `null`** (extraction-time `source=auto` pushes), fall back to your model-side default negative. |
| `mode` | `with_reference` if a reference image should be supplied; `no_reference` otherwise. |
| `reference_s3_key` | When `mode=with_reference`, fetch this from S3 and pass to the model as IP-adapter / reference input. Null otherwise. |
| `preset` | The active preset slug. Informational for Node — the prompt already encodes the preset modifier. Use for analytics / logs. |
| `source` | Why this composition was produced. For analytics. |
| `composed_at` | Stale-trigger detection. See §11. |

### 6.2 What Node does NOT read for prompt-to-send

- **`<table>.generation_prompt`** (top-level TEXT column). This is the
  immutable LLM-emitted base scene description. It does NOT include the
  user's stacked edits or the preset modifier. Reading it would silently
  drop everything the user composed. Use `latest_generation_context.prompt`
  instead.

### 6.3 Reference image (when `mode=with_reference`)

Fetch from S3 at the `reference_s3_key` path. Pass to the image model
as the IP-adapter / reference input. See §9 for the layout.

---

## 7. Postgres writes Node performs

**Only the URL columns. Nothing else.**

```sql
UPDATE persons       SET image_url = $1, thumbnail_url = $2          WHERE id = $3;
UPDATE moments       SET video_url = $1, thumbnail_url = $2          WHERE id = $3;
UPDATE entities      SET image_url = $1                              WHERE id = $3;
UPDATE threads       SET image_url = $1                              WHERE id = $3;
```

(Schema differs by table — `moments` have `video_url + thumbnail_url`,
others have `image_url` only. Confirm against `migrations/0001_initial_schema.up.sql`
before writing the SQL.)

### Hard rules

- ❌ Never write `generation_prompt`. Owned by the agent.
- ❌ Never write `latest_generation_context`. Owned by the agent.
- ❌ Never write any other column on these tables. Owned by the agent.
- ✅ URL columns are yours.

---

## 8. DynamoDB edit-history table (Node-owned)

The agent stays stateless on edit history. Node owns it.

### Table: `flashback_artifact_edits`

```
PK: composite { record_type, record_id }

attributes:
  person_id:                 string  (UUID)
  prior_instructions:        list<string>   # oldest first
  last_reference_s3_key:     string?         # most recent uploaded reference
  last_preset:               string?         # most recent preset slug
  updated_at:                string  (ISO-8601 UTC)
  source_of_last_update:     "regenerate" | "edit"
```

One row per artifact-bearing record. Covers `record_type ∈ {person, moment, entity, thread}` — same table.

### Lifecycle

| User action | Read | Agent call | Write after agent 2xx |
|---|---|---|---|
| Opens edit modal | Read row → pre-fill chips, preset, reference | — | — |
| Submits `/edit` | Read row → get `prior_instructions` to send | `POST /...edit` with current `prior_instructions` + new `instructions` | Append `instructions` to `prior_instructions`. Update `last_preset`, `last_reference_s3_key`, `updated_at = now()`, `source_of_last_update = "edit"`. |
| Clicks "Regenerate" with preset | Read row → pre-fill preset/reference selector | `POST /...regenerate` with selected `preset` + optional `reference_s3_key` | Update `last_preset`, `last_reference_s3_key`, `updated_at`, `source_of_last_update = "regenerate"`. **Do not touch `prior_instructions`** — regenerates are stylistic re-rolls, not history resets. |
| Clicks "Reset edits" (confirm) | — | (No agent call; agent will pick up `prior_instructions=[]` on the next edit / regenerate) | Set `prior_instructions = []`. Keep other fields. |
| Removes a chip from the modal mid-edit | (Local state only; persist on submit) | On submit, send `prior_instructions` with the removed entry filtered out | Replace `prior_instructions` with the filtered list. |

### Failure handling

- If the agent call returns non-2xx, **do not** mutate Dynamo. Keep state aligned with what was actually enqueued.
- If Dynamo write fails after a 2xx, log it. The agent has already enqueued; the artifact will generate; the worst case is the next edit modal shows slightly stale chip state.

### IAM

Node's API service: `dynamodb:GetItem`, `PutItem`, `UpdateItem`, `DeleteItem` on this table.
Node's SQS worker: no access needed (worker doesn't touch Dynamo).

---

## 9. S3 layout

### 9.1 User-uploaded references (NEW)

When the user uploads a reference photo on the edit / regenerate modal:

```
s3://<your-bucket>/uploads/{user_id}/{record_type}/{record_id}/{ts}.{ext}
```

| Segment | Notes |
|---|---|
| `user_id` | Same as `person_id` (the legacy owner). |
| `record_type` | `moment` / `entity` / `person`. Threads excluded — agent 400s if you try. |
| `record_id` | The record's UUID. For portraits this equals `user_id`. |
| `ts` | Unix timestamp (millis or seconds, your call). |
| `ext` | `jpg` / `png` / `webp`. |

Size cap: ≤ 10 MB recommended. Type validation: client-side first, then server-side magic-byte check.

Pass the resulting key (full path minus the bucket) as `reference_s3_key` on the agent endpoint.

### 9.2 Generated artifacts (existing layout — your choice)

After the worker generates, upload to your existing per-record layout, e.g.:

```
s3://<your-bucket>/profile-pictures/{person_id}/{job_id}.png
s3://<your-bucket>/profile-pictures/{person_id}/{job_id}_thumb.png
s3://<your-bucket>/moments/{moment_id}/{job_id}.mp4
s3://<your-bucket>/moments/{moment_id}/{job_id}_thumb.png
s3://<your-bucket>/entities/{entity_id}/{job_id}.png
s3://<your-bucket>/threads/{thread_id}/{job_id}.png
```

The agent doesn't care about this layout; only Node and the frontend
need to agree.

### 9.3 IAM

Node's API service: `s3:PutObject` on `uploads/*`.
Node's SQS worker: `s3:GetObject` on `uploads/*`, `s3:PutObject` on result paths.
Agent service: **no S3 access at all.** (Confirms §3 boundary in CLAUDE.md.)

---

## 10. User-facing UI — what to build

### 10.1 Regenerate ▾ split control

Replace each existing "Regenerate" button with a split control:

- **Primary button (`Regenerate`)** → calls `/artifacts/.../regenerate` (or `/persons/{id}/profile-picture` for portraits) with no preset (defaults to `painterly_cinematic`).
- **Dropdown arrow** → opens a menu listing presets from `GET /artifact-presets`.
  - Default preset shown with a subtle "Default" tag.
  - User picks a slug → call regenerate with that preset.
- **Below the menu** (for moments + entities only): "Use a reference photo →" link that opens the upload modal.
- **For threads**: no reference-photo link. The agent rejects.

### 10.2 Edit modal

A single multi-line freeform input (max 500 chars), with:

- **Above the input:** chip list showing the current `prior_instructions` from Dynamo. Each chip is removable with a small ✕. The user sees what's already layered on this artifact.
- **Below the input:**
  - Preset picker (same five slugs).
  - Reference-image upload affordance (moments + entities only).
- **Save** button → calls `/edit` with `prior_instructions` (after any chip removals) + the new `instructions` + chosen `preset` + `reference_s3_key`.
- **"Reset edits"** link, visually separate from Cancel. On click, confirm modal; if confirmed, clear `prior_instructions: []` in Dynamo and refresh the chip list (now empty). The next regenerate / edit starts clean.

### 10.3 Reference-image upload UX

- Drag-and-drop zone + file picker.
- Client-side validation: size (≤ 10 MB), type (image/jpeg, image/png, image/webp).
- Show a small preview thumbnail after upload.
- On submit, the upload happens to S3, the key is captured, and it's sent with the edit / regenerate call.
- For moments and entities only. Hide the affordance for threads.

### 10.4 Loading / status display

- After submit, the artifact takes seconds to minutes depending on model + queue depth.
- Recommended: show a spinner on the artifact card with text like "Re-generating…" and poll the record's `image_url` / `video_url` every ~3 seconds (or use a websocket if you have one) until it updates.
- The agent has no completion signal — Node's worker writing the URL column IS the completion signal.

### 10.5 Empty / error states

- Network error on submit: show inline error, keep modal open with state preserved.
- Agent 400 (bad preset, etc.): treat as a frontend bug — log it, show generic error.
- Agent 404 (record not found): refresh the page; the record may have been superseded.
- Worker failure / DLQ: surface via your existing job-failure UI (whatever convention Node already uses).

---

## 11. Worker pseudocode

Same algorithm for both queues. Pseudocode (TypeScript-ish):

```typescript
async function handleArtifactTrigger(msg: SQSMessage) {
  const trigger = JSON.parse(msg.Body);
  const { job_id, record_type, record_id, person_id,
          artifact_kind, source, composed_at } = trigger;

  // 1. Validate trigger.
  if (!ALLOWED_RECORD_TYPES.includes(record_type)) {
    logger.warn("unknown record_type", { record_type, job_id });
    return deleteMessage(msg);  // bad trigger, nothing to do
  }

  // 2. Read context from Postgres.
  const table = TABLE_FOR_RECORD_TYPE[record_type];  // moments / entities / threads / persons
  const ctx = await pg.queryOne(
    `SELECT latest_generation_context FROM ${table}
      WHERE id = $1 AND status = 'active'`,
    [record_id]
  );
  if (!ctx) {
    logger.info("record gone or superseded", { record_type, record_id, job_id });
    return deleteMessage(msg);  // row deleted or superseded; trigger is moot
  }

  // 3. Stale-trigger detection.
  if (ctx.composed_at > composed_at) {
    logger.info("trigger superseded", {
      record_id,
      message_composed_at: composed_at,
      row_composed_at: ctx.composed_at,
    });
    return deleteMessage(msg);  // a later push already generates the latest
  }

  // 4. Optional: fetch reference image.
  let referenceImage = null;
  if (ctx.mode === "with_reference" && ctx.reference_s3_key) {
    referenceImage = await s3.getObject(BUCKET, ctx.reference_s3_key);
  }

  // 5. Generate.
  const negative = ctx.negative_prompt ?? MODEL_DEFAULT_NEGATIVE;
  const artifactBytes = await callImageModel({
    prompt: ctx.prompt,
    negative_prompt: negative,
    reference_image: referenceImage,
    artifact_kind,           // image or video — pick the right model
  });

  // 6. Upload result + write URL.
  const urls = await uploadAndUrls(record_type, record_id, job_id, artifactBytes);

  if (record_type === "person") {
    await pg.execute(
      `UPDATE persons SET image_url = $1, thumbnail_url = $2 WHERE id = $3`,
      [urls.full, urls.thumb, record_id]
    );
  } else if (record_type === "moment") {
    await pg.execute(
      `UPDATE moments SET video_url = $1, thumbnail_url = $2 WHERE id = $3`,
      [urls.full, urls.thumb, record_id]
    );
  } else if (record_type === "entity") {
    await pg.execute(
      `UPDATE entities SET image_url = $1 WHERE id = $3`,
      [urls.full, record_id]
    );
  } else if (record_type === "thread") {
    await pg.execute(
      `UPDATE threads SET image_url = $1 WHERE id = $3`,
      [urls.full, record_id]
    );
  }

  // 7. Delete from SQS.
  await deleteMessage(msg);
}
```

`composed_at` comparison: both timestamps are ISO-8601 UTC; string
comparison works correctly (lexicographic == chronological for that
format). If you want to be safe, parse to `Date` and compare numerically.

---

## 12. Versioning, rollout, deploy ordering

### 12.1 What is versioned and how

| Surface | Versioning mechanism | Stability guarantee |
|---|---|---|
| Postgres schema | Numbered migrations under `migrations/` (0001 … 0023). | Migration `0023` adds `latest_generation_context`. Node depends on migration `>= 0023`. |
| HTTP endpoints | No `/v1` prefix today. Breaking changes are coordinated. | Breaking changes get a `CHANGELOG` entry and a heads-up to Node. |
| SQS payload shape | No `schema_version` field today. **Recommended addition** — see §12.4. | We rev the shape together. This rollout is the one breaking change in flight. |
| Preset slugs | Stable strings. Adding new slugs is non-breaking; removing or renaming is. | `GET /artifact-presets` is the discovery surface. Don't hardcode the list. |
| Prompt provenance | Slugs like `node_edits.moment.v2` written to row metadata for audit. | Internal to the agent; Node doesn't read these. |

### 12.2 Deploy ordering (critical)

This rollout requires deploys to happen in this order to avoid data inconsistency:

1. **Apply migration `0023`** on production Postgres. Backfills existing rows' `latest_generation_context` from their `generation_prompt`. Idempotent — safe to re-run.
2. **Deploy the agent service** (already has the new code). After this point, every artifact push writes `latest_generation_context` and emits trigger-only SQS messages.
3. **Drain the old SQS messages** that were in-flight at the start of step 2. They have the rich payload Node's current worker expects. Either:
   - Let Node's current worker process them with old-shape logic (don't switch worker code yet).
   - Or pause the workers, drain via a one-off script, then proceed.
4. **Deploy Node's new SQS worker** that reads from `latest_generation_context`. From this point on, only trigger-only payloads are in flight.
5. **Deploy Node's API + frontend** with the new edit / regenerate UI.

### 12.3 Rollback strategy

If something is wrong after step 2 / 4:

- **Migration `0023` down-script** (`migrations/0023_latest_generation_context.down.sql`) drops the new column. Run only if the agent service is reverted in lock-step.
- **Agent service**: redeploy the pre-rollout build. The old build still pushes the rich SQS payload, so Node's old worker handles it. Postgres has an orphan column (`latest_generation_context`) but that's harmless.
- **Node SQS worker**: roll back to the pre-rollout worker if you've already deployed the new one. As long as the agent is also pre-rollout, the payloads match.

### 12.4 Recommended: add a `schema_version` field to SQS payloads

The current payload doesn't carry an explicit version. Suggest adding
`"schema_version": "2"` to the trigger payload so Node's worker can
detect and reject older / newer shapes during transitions. We don't
have this today — it'd be a small follow-up commit on the agent side
if you want it. Worth doing.

### 12.5 Backward compatibility — what we kept

- The Postgres `generation_prompt` column is unchanged in meaning. It's still the immutable LLM-emitted base. Anything that read it before (analytics, debugging, the agent's own composition layer) still works.
- The agent's HTTP endpoints that existed pre-rollout still accept the same request shapes (we added optional fields, never removed required ones).
- The two SQS queues are the same physical queues (`flashback_agent-artifact-generation`, `flashback_agent-profile-picture`). Only the payload shape changed.

### 12.6 Backward compatibility — what we broke

- SQS payload shape on both queues. Old payloads in flight at deploy time will fail to parse under the new worker (no version field to discriminate). See §12.2 for the deploy ordering that avoids this.

---

## 13. Infrastructure setup

### 13.1 SQS

Both queues already exist:

- `flashback_agent-artifact-generation` — `ARTIFACT_QUEUE_URL`
- `flashback_agent-profile-picture` — `https://sqs.ap-south-1.amazonaws.com/768699754860/flashback_agent-profile-picture`

Recommended attributes:

- **Visibility timeout:** ≥ 2× the p95 model inference time. If your model takes ≤ 2 min, set to 5 min. Tune based on observed retries.
- **Message retention:** 4 days (default). Lower if you want faster DLQ rotation.
- **DLQ:** Configure a redrive policy with max-receives = 3. Failed messages land in `flashback_agent-artifact-generation-dlq` for manual inspection. Same for the portrait queue.
- **Long polling:** `WaitTimeSeconds=20` on receive.

### 13.2 DynamoDB

Table `flashback_artifact_edits` (see §8). Recommended config:

- **Billing mode:** On-demand (low volume; bursty user activity).
- **Encryption:** SSE with AWS-owned keys (cheapest).
- **PITR:** Enable; edit history is user-visible and worth recovering on incident.
- **Backup:** Daily snapshot retained 7 days.

### 13.3 IAM

Two service roles needed on the Node side. (Reuse existing roles if you have them — these are just the new permissions to add.)

**Node API service** (the request handler that calls the agent and writes Dynamo):

```
- s3:PutObject       on  arn:aws:s3:::<bucket>/uploads/*
- dynamodb:GetItem
  dynamodb:PutItem
  dynamodb:UpdateItem
  dynamodb:DeleteItem on  arn:aws:dynamodb:<region>:<account>:table/flashback_artifact_edits
```

**Node SQS worker service** (the drainer):

```
- sqs:ReceiveMessage
  sqs:DeleteMessage
  sqs:GetQueueAttributes on  both queue ARNs

- s3:GetObject  on  arn:aws:s3:::<bucket>/uploads/*
- s3:PutObject  on  arn:aws:s3:::<bucket>/profile-pictures/*  (and moments/entities/threads/* per §9.2)

- (RDS / Postgres) read/write specific tables — your existing Postgres-access role
- ❌ NO write access to <table>.generation_prompt or <table>.latest_generation_context.
  This is enforced by convention + code review, not by IAM — Postgres doesn't do column-level perms cleanly.
```

### 13.4 CloudWatch / metrics

Per-queue metrics worth alerting on:

- `ApproximateNumberOfMessagesVisible` rising > 100 (backlog).
- `ApproximateNumberOfMessagesNotVisible` > 50 for 10+ min (jobs stuck in flight, possibly stuck workers).
- DLQ message count > 0 (always alert).

Per-job logs (worker side): emit a structured log per message processed with `{job_id, record_type, record_id, source, composed_at, latency_ms, outcome}`. Surfaces production behavior and lets you correlate trigger → result.

### 13.5 Frontend env vars

If frontend uploads references directly to S3 (signed URL flow), you'll need:

- `S3_BUCKET` (or its CDN equivalent).
- A signed-URL endpoint on Node that issues presigned PUT URLs for `uploads/{user_id}/...` paths.

---

## 14. Error handling, retries, idempotency

### 14.1 Worker retries

- SQS handles retry via visibility timeout. A worker crash mid-processing → the message reappears after the timeout, re-delivered.
- After 3 retries (configurable), the message moves to DLQ. Alert on DLQ depth > 0.
- **Idempotency requirement:** generating the same `job_id` twice should be safe. Upload the result to a deterministic S3 key (`{job_id}.png`) — overwriting is fine. The URL column write is idempotent (overwrites the same value).

### 14.2 Stale triggers

Already covered in §11. If `ctx.composed_at > message.composed_at`, the
trigger is stale (a later push superseded it). Skip + delete. Don't
generate.

### 14.3 Race: two edits in fast succession

User submits edit-1 → agent writes context (composed_at=T1) → pushes trigger M1.
User submits edit-2 a second later → agent overwrites context (composed_at=T2) → pushes trigger M2.

Worker drains M1 first → reads context (composed_at=T2 now) → trigger composed_at=T1 < T2 → **skip M1**.
Worker drains M2 → reads context (composed_at=T2) → trigger composed_at=T2 == T2 → **generate**.

Result: one artifact generated, matching the latest user intent. Both messages get deleted.

### 14.4 Race: edit during in-flight regenerate

User clicks regenerate → trigger M1 (composed_at=T1) → worker starts generating with context T1.
User submits edit before M1 completes → agent overwrites context (composed_at=T2) → pushes trigger M2.

Worker finishes M1 → writes URL → message deleted.
Worker drains M2 → context.composed_at=T2 > M1's T1 → generates with T2 context → overwrites URL.

Result: two artifacts generated, latter wins. Slightly wasteful but correct. If you want to avoid the wasted compute, implement an "abort in-flight job if newer context is detected" mechanism — but it's complexity for a rare edge case.

### 14.5 Agent unavailable / HTTP error

If `/artifacts/.../edit` returns 5xx, retry with exponential backoff (3 attempts, then surface error to user). **Do not** mutate Dynamo on failure — keep history aligned with what was actually enqueued.

### 14.6 Postgres write failure (URL columns)

Retry. The artifact bytes are already in S3, so re-running the write is safe — S3 is idempotent by key.

### 14.7 Reference-image gone (S3 404)

If `mode=with_reference` but `s3:GetObject` 404s on `reference_s3_key`, log a warning and fall back to `mode=no_reference` (generate without the reference). Don't fail the job. Edge case: user deleted the upload between submit and worker drain — rare but possible.

---

## 15. Acceptance checklist

Use this to gate the merge / release.

**Infrastructure (do these first):**

- [ ] Postgres migration `0023` applied to dev + staging + prod.
- [ ] DynamoDB table `flashback_artifact_edits` exists in dev + staging + prod with PITR enabled.
- [ ] SQS visibility timeouts tuned for model inference time.
- [ ] DLQs configured on both queues; CloudWatch alarm on DLQ depth > 0.
- [ ] IAM roles updated per §13.3.
- [ ] S3 `uploads/` path layout supported by the bucket; signed-URL endpoint live (if using).

**Backend wiring:**

- [ ] `GET /artifact-presets` is fetched once per session and cached (24h TTL).
- [ ] All four agent endpoints (`/artifacts/.../regenerate`, `/artifacts/.../edit`, `/persons/.../profile-picture[/edit]`) are exposed through Node's API layer to the frontend.
- [ ] Node API forwards `prior_instructions` from Dynamo on every `/edit` call.
- [ ] Node API appends to Dynamo `prior_instructions` only after the agent returns 2xx.
- [ ] Node API does NOT touch `prior_instructions` on `/regenerate` calls.
- [ ] "Reset edits" flow clears Dynamo `prior_instructions` to `[]`.

**Worker:**

- [ ] Both SQS workers parse the trigger-only payload (8 fields).
- [ ] Worker fetches `latest_generation_context` from Postgres via the correct table for the `record_type`.
- [ ] Worker honors the `composed_at` stale-check (skip + delete if row's value is newer).
- [ ] Worker uses `context.negative_prompt` when present; falls back to model-side default when null.
- [ ] Worker fetches `context.reference_s3_key` from S3 when `mode=with_reference`; falls back to no_reference if the upload is gone (404).
- [ ] Worker writes ONLY the URL columns back to Postgres. Never `generation_prompt` or `latest_generation_context`.
- [ ] Worker deletes the SQS message on success.
- [ ] Worker emits a structured log per message processed.

**Frontend:**

- [ ] Regenerate ▾ split control on each artifact card with preset dropdown from `/artifact-presets`.
- [ ] Edit modal shows current `prior_instructions` as removable chips, accepts a new edit, supports preset + reference upload.
- [ ] "Reset edits" affordance with confirmation.
- [ ] Reference upload affordance hidden for `record_type=thread`.
- [ ] Loading / polling state on artifact cards until the URL updates.

**Validation:**

- [ ] End-to-end test: create a person → onboarding portrait generated → edit "give him glasses" → portrait regenerated with glasses → edit "and a brown coat" → portrait shows both glasses and brown coat (edit stacking works).
- [ ] End-to-end test: upload a reference photo of a house → moment image regenerated with that house as visual anchor.
- [ ] Stale-trigger test: submit two edits within 5s → only the latest generates (older trigger skipped).
- [ ] DLQ test: force a worker error → message lands in DLQ → alarm fires.

---

## 16. Known issues + mitigations

### 16.1 Deity / cultural-name collisions in portraits — mitigated agent-side

Names like Krishna, Jesus, Buddha, Ganesh, Apollo, Athena used to produce
deity renderings instead of ordinary people with those names. Agent now
includes:

- A real-person anchor at the start of every portrait prompt.
- A stronger relationship clause ("an ordinary contemporary friend").
- Negative-prompt terms for deity tells (halo, multi-arm, blue/green skin, peacock-feather crown, divine aura).

Node does not need to do anything. If a deity rendering still slips
through, file a bug with the agent team — we have escalation options.

### 16.2 Short-edit collapse on `node_edits` — mitigated agent-side

Editing a moment with a short note like "It was RDR2-like." used to
sometimes collapse the entire narrative into the four words. Agent now
runs a merge-first prompt (`node_edits.moment.v2`) that defaults to
preserving prior content and only treats explicit contradictions as
removals. Same fix on entity edits.

Node does not need to do anything. If a collapse still happens, file a
bug — we have deterministic-merge fallbacks available.

### 16.3 Visibility timeout vs. long inference — operational

If your model occasionally takes longer than the SQS visibility timeout,
the message is re-delivered and a second worker picks it up while the
first is still processing. Both will write to the same `job_id` S3 key
and the same URL columns — idempotent, so it works, but it wastes
compute. Tune visibility timeout based on observed p99 inference time
× 2.

### 16.4 `generation_prompt` vs. `latest_generation_context.prompt` — easy mistake

The TOP-LEVEL `generation_prompt` column on each row is the immutable
LLM-emitted base. Reading it for the prompt-to-send drops every user
edit + preset modifier silently. **Always read
`latest_generation_context.prompt`**, never the top-level column.

The agent code has automated tests + a hard rule in CLAUDE.md to prevent
the equivalent mistake on the agent side. Node's worker should have a
unit test that asserts the right read path.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Auto path** | Artifact pushes initiated by the extraction worker or thread detector (no user interaction). `source=auto`. |
| **Composed prompt** | The full prompt the model sees: base scene description + stacked user edits + preset modifier. Lives in `latest_generation_context.prompt`. |
| **Base prompt** | The immutable LLM-emitted scene description. Lives in the row's top-level `generation_prompt`. |
| **`composed_at`** | UTC timestamp stamped when the agent wrote `latest_generation_context`. Used for stale-trigger detection. |
| **DLQ** | Dead-letter queue. Failed messages after N retries. |
| **Edit stacking** | The pattern where each `/edit` call carries the full cumulative `prior_instructions` (Node tracks in Dynamo), so the composed prompt always contains every accepted edit. |
| **`latest_generation_context`** | The JSONB column on every artifact-bearing table holding the composed prompt + negative + mode + reference + preset + source + composed_at. Added in migration 0023. |
| **Postgres-authoritative** | The architecture where the row's `latest_generation_context` is the source of truth for the prompt; SQS is a trigger only. |
| **Preset** | A user-pickable style slug (`painterly_cinematic`, `golden_hour`, etc.) that appends a stylistic modifier to the composed prompt. |
| **Reference image** | A user-uploaded photo used as IP-adapter / reference input for moment + entity image generation. Threads don't support it. |
| **Source** | Why a job was generated: `auto` / `onboarding` / `regenerate` / `edit`. |
| **Stale trigger** | An SQS message whose `composed_at` is older than the row's current `latest_generation_context.composed_at`. Skip + delete. |
| **Trigger-only payload** | The 8-field SQS message shape (`job_id`, `record_type`, `record_id`, `person_id`, `artifact_kind`, `source`, `composed_at`, `enqueued_at`). |

---

## 18. Cross-references

- [docs/backend-profile-picture-queue.md](backend-profile-picture-queue.md) — portrait queue contract (the narrower spec)
- [docs/backend-artifact-edit-queue.md](backend-artifact-edit-queue.md) — artifact queue contract (the narrower spec)
- [docs/node-integration-artifact-edits.md](node-integration-artifact-edits.md) — earlier consolidated brief (superseded by this doc)
- [CLAUDE.md](../CLAUDE.md) §3 — repo-level boundary rules; §9 — agent API surface
- [migrations/0023_latest_generation_context.up.sql](../migrations/0023_latest_generation_context.up.sql) — the schema change

---

## 19. One-shot prompt for an AI assistant

If you want to hand this off to a fresh AI session, paste this:

> You are implementing the Node.js Backend side of Flashback's
> artifact-generation v2 rollout. The Python agent service is already
> on the new model; Node needs to catch up. The agent service is
> **Postgres-authoritative**: it composes every prompt and writes the
> full context to `<table>.latest_generation_context` JSONB on every
> artifact-bearing row BEFORE pushing the SQS message. The SQS
> messages are now **trigger-only** (8 fields, no prompt content).
>
> Your job:
>
> 1. Apply Postgres migration 0023 (adds `latest_generation_context`).
> 2. Create DynamoDB table `flashback_artifact_edits` keyed by
>    `(record_type, record_id)`, storing `prior_instructions:
>    list<string>`, `last_reference_s3_key`, `last_preset`,
>    `updated_at`. Same table for all four record types
>    (`person`/`moment`/`entity`/`thread`).
> 3. Build S3 upload UX for reference photos under
>    `uploads/{user_id}/{record_type}/{record_id}/{ts}.{ext}`. Skip
>    for threads.
> 4. Update both SQS workers
>    (`flashback_agent-artifact-generation` +
>    `flashback_agent-profile-picture`) with the new flow:
>    parse trigger → `SELECT latest_generation_context FROM <table>
>    WHERE id = $1 AND status='active'` → stale check (skip if row's
>    composed_at > message's) → generate using
>    `context.prompt`/`context.negative_prompt` (model default
>    fallback if null) + reference from S3 if
>    `mode='with_reference'` → write URL columns → delete SQS
>    message.
> 5. Build the frontend: replace each "Regenerate" button with a
>    split control that lists presets from `GET /artifact-presets`.
>    Add an edit modal showing current `prior_instructions` from
>    Dynamo as removable chips, accepting a new edit, supporting
>    preset + optional reference upload. Add "Reset edits"
>    affordance.
> 6. After every successful `/edit` call append the new instruction
>    to Dynamo; after `/regenerate` only update
>    `last_preset`/`last_reference_s3_key`; never touch
>    `prior_instructions` on regenerate; clear it on "Reset edits".
>
> Honor `CLAUDE.md` §3 boundaries: Node writes ONLY the URL columns
> on the Postgres rows. Never write `generation_prompt` or
> `latest_generation_context`. Never compose prompts on the Node
> side. Pass `context.prompt` and `context.negative_prompt` straight
> to the model.
>
> The full spec including response shapes, worker pseudocode,
> versioning + rollout plan, IAM policies, and the acceptance
> checklist lives at `docs/NODE_INTEGRATION_PROMPT.md` in the agent
> repo.
