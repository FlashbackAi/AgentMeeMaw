# Backend Services: Artifact Generation Queue (moments, entities, threads)

**For:** Node.js Backend team
**Queue name:** `flashback_agent-artifact-generation` (existing — `ARTIFACT_QUEUE_URL`)
**Queue type:** Standard SQS

This is the queue the extraction worker has been pushing first-time
artifact jobs onto since v1. Under the Postgres-authoritative model, the
SQS message is **a trigger only**. The agent composes the full
generation context (prompt + negative + mode + reference key + preset)
and writes it to `<table>.latest_generation_context` on the originating
row BEFORE pushing. The worker reads context from Postgres at job time.

---

## Prompt source — Postgres is authoritative

**Read every artifact-generation field from Postgres**, not from the SQS
message. The agent fully owns prompt composition (base + cumulative
`prior_instructions` + newest `instructions` + preset modifier + negative
prompt). On every push it writes a JSONB blob to
`<table>.latest_generation_context` before sending the trigger.

Node:

- **Reads** the trigger message: figure out *which row* needs an artifact.
- **Reads** `<table>.latest_generation_context` from Postgres at processing
  time — that's where the prompt, negative, mode, reference, and preset
  live.
- **Does NOT** read `generation_prompt` from the row's top-level column
  for the prompt-to-send. That column carries only the LLM-emitted base
  scene description; the composed-with-edits-and-preset version is in
  `latest_generation_context.prompt`.
- **Does NOT** mutate, decorate, prepend, or append to the prompt on the
  Node side. Any style adjustment must round-trip through the preset
  registry on the agent so all callers see the same shape.

The trigger payload includes a `composed_at` timestamp matching
`latest_generation_context.composed_at`. If a later edit / regenerate
has superseded the context (i.e. the row's `composed_at` is newer than
the message's), the worker can choose to skip the older trigger — only
the latest composition needs to be generated.

---

## Trigger payload (SQS message body)

```json
{
  "job_id":        "<uuid>",
  "record_type":   "moment" | "thread" | "entity",
  "record_id":     "<uuid>",
  "person_id":     "<uuid>",
  "artifact_kind": "image" | "video",
  "source":        "auto" | "regenerate" | "edit",
  "composed_at":   "<ISO-8601 UTC — matches latest_generation_context.composed_at>",
  "enqueued_at":   "<ISO-8601 UTC>"
}
```

### Field notes

| Field | Notes |
|---|---|
| `job_id` | Fresh UUID per push. Use for idempotency / dedup if your worker layer needs it. |
| `record_type` | `moment` → video, `entity` → image, `thread` → image. `person` does NOT route through this queue — see [backend-profile-picture-queue.md](backend-profile-picture-queue.md). |
| `record_id` | The row's PK. Use it to `SELECT latest_generation_context FROM <table> WHERE id = %s` (table derived from `record_type`). |
| `artifact_kind` | Image or video, derived from `record_type`. Use to pick which model + S3 layout to apply. |
| `source` | `auto` — pushed by the extraction worker / thread detector on first-time creation. `regenerate` — user picked a preset / uploaded a reference. `edit` — user typed an instruction. |
| `composed_at` | Matches the row's `latest_generation_context.composed_at` at push time. If the row's current value is newer, this trigger is stale (a later edit / regenerate superseded it); the worker may skip and `DeleteMessage`. |
| `enqueued_at` | When the agent pushed the SQS message. For latency tracking. |

---

## `latest_generation_context` JSONB (Postgres)

```json
{
  "prompt":            "<composed prompt — pass directly to the image / video model>",
  "negative_prompt":   "<scene-art negative>" | null,
  "mode":              "no_reference" | "with_reference",
  "reference_s3_key":  "<s3 key>" | null,
  "preset":            "<preset slug>" | null,
  "source":            "auto" | "regenerate" | "edit",
  "composed_at":       "<ISO-8601 UTC>"
}
```

Lives on every artifact-bearing table (`moments`, `entities`, `threads`,
and `persons` for the profile-picture flow). Populated by migration 0023
on existing rows; written fresh by the agent on every new push.

### Field notes

| Field | Notes |
|---|---|
| `prompt` | Already composed by the agent: base + stacked user edits + preset modifier. Pass directly to the image / video model. |
| `negative_prompt` | For user-initiated regenerate/edit jobs this carries the scene-art negative (blocks cartoon shading, deepfake likeness, watermarks). For `source=auto` extraction pushes this is `null` — the agent omits a negative for those and you can apply your model-side default. |
| `mode` | `with_reference` if `reference_s3_key` is set; `no_reference` otherwise. |
| `reference_s3_key` | S3 key of a user-uploaded photo. Allowed for `moment` and `entity`; threads reject reference uploads at the HTTP layer. |
| `source` | Mirrors the trigger payload's `source`. |
| `composed_at` | Stamped by the agent at composition time. Used for stale-trigger detection (see above). |

---

## What Node needs to build

### 1. Extend the existing artifact-generation worker

For each message:

1. **Parse** the trigger payload. Extract `record_type`, `record_id`, `composed_at`.
2. **Read context from Postgres:**
   ```sql
   SELECT latest_generation_context
     FROM <table>            -- moments / entities / threads
    WHERE id = %s AND status = 'active'
   ```
   If `latest_generation_context IS NULL` or the row is missing, log + skip + delete the SQS message (race: row was superseded / deleted before the trigger drained).
3. **Stale check (optional but recommended):** if `context.composed_at > message.composed_at`, a later push has superseded this trigger — skip + delete.
4. **Generate the image / video:**
   - Call the model with `context.prompt` and `context.negative_prompt` (fall back to your model-side default negative if null).
   - If `context.mode = 'with_reference'`, fetch `context.reference_s3_key` from S3 and supply as IP-adapter / reference input.
5. **Upload to S3** under your existing per-record key scheme.
6. **Write back to Postgres** by writing the appropriate URL column (`image_url`, `video_url`, `thumbnail_url`) on the originating row (`record_id`). Do **not** touch `generation_prompt` or `latest_generation_context` — the agent owns those.
7. **Delete the SQS message** on success.

### 2. Per-record edit-history table in Dynamo

The agent stays stateless. Node owns per-record edit history. Recommended shape (one shared table, keyed by record_type + record_id):

```
PK: { record_type, record_id }
attributes:
  prior_instructions:    [string, ...]   # oldest first
  last_reference_s3_key: string?         # most recent uploaded reference
  last_preset:           string?         # most recent preset slug
  updated_at:            iso-8601
```

When the user submits an edit:

1. Read the row by `(record_type, record_id)` to get the current `prior_instructions`.
2. POST to `/artifacts/{record_type}/{record_id}/edit` with body:
   ```json
   {
     "person_id": "...",
     "instructions": "<new edit text>",
     "prior_instructions": ["<oldest>", ..., "<most recent prior>"],
     "preset": "<slug>" | null,
     "reference_s3_key": "<key>" | null
   }
   ```
3. On 200 (the agent wrote context to Postgres + enqueued a trigger), append `instructions` to `prior_instructions` in Dynamo. If the call fails, do not append.

When the user clicks "Regenerate" with a preset:

1. POST to `/artifacts/{record_type}/{record_id}/regenerate` with `{ person_id, preset, reference_s3_key? }`.
2. **Do not** clear `prior_instructions` — a regenerate is a stylistic re-roll, not a history reset. The agent only stacks instructions on `/edit` calls.

When the user wants a clean restart (no prior edits applied):

- Treat as a regenerate (above), or call `/edit` with `prior_instructions: []`.
- Clear `prior_instructions` in Dynamo client-side to match.

### 3. Reference-image upload UX

For `moment` and `entity` images the user can upload a real-world photo
("here's the actual house") to steer the regeneration. Flow:

1. Node uploads to S3 under e.g. `uploads/{user_id}/{record_type}/{record_id}/{ts}.jpg`.
2. Node sends the resulting `reference_s3_key` to the agent on `/regenerate` or `/edit`.
3. Persist `last_reference_s3_key` in the Dynamo edit-history row so subsequent edits can re-use it.
4. Threads do not support reference uploads — the agent returns 400.
