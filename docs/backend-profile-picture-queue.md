# Backend Services: Profile-Picture Generation Queue

**For:** Node.js Backend team
**Queue name:** `flashback_agent-profile-picture`
**Queue URL:** `https://sqs.ap-south-1.amazonaws.com/768699754860/flashback_agent-profile-picture`
**Queue type:** Standard SQS (not FIFO)

Under the Postgres-authoritative model, the SQS message is **a trigger
only**. The agent composes the full portrait context (prompt + negative
+ mode + reference key + preset) and writes it to
`persons.latest_generation_context` BEFORE pushing. The worker reads
context from Postgres at job time.

---

## Prompt source — Postgres is authoritative

**Read every portrait field from Postgres**, not from the SQS message.
The agent fully owns prompt composition (portrait recipe + relationship
anchor + stacked `prior_instructions` + newest `instructions` + preset
modifier + deity-iconography negative). On every push it writes a JSONB
blob to `persons.latest_generation_context` before sending the trigger.

Node:

- **Reads** the trigger message: figure out *which person* needs a portrait.
- **Reads** `persons.latest_generation_context` from Postgres at processing time — that's where the prompt, negative, mode, reference, and preset live.
- **Does NOT** mutate or decorate the prompt on the Node side. Style adjustments must round-trip through the preset registry on the agent.

The trigger payload includes a `composed_at` timestamp matching
`latest_generation_context.composed_at`. If a later edit / regenerate has
superseded the context (row's `composed_at` is newer), the worker can
choose to skip the older trigger — only the latest composition needs to
be generated.

---

## Trigger payload (SQS message body)

```json
{
  "job_id":        "<uuid>",
  "record_type":   "person",
  "record_id":     "<person_id>",
  "person_id":     "<person_id>",
  "artifact_kind": "image",
  "source":        "onboarding" | "regenerate" | "edit",
  "composed_at":   "<ISO-8601 UTC — matches latest_generation_context.composed_at>",
  "enqueued_at":   "<ISO-8601 UTC>"
}
```

### Field notes

| Field | Notes |
|---|---|
| `job_id` | For `source=onboarding` this is the stable `person_id` (one job per person creation). For `regenerate`/`edit` it is a fresh UUID — multiple jobs per person. |
| `record_id` / `person_id` | Both equal the Postgres `persons.id` UUID. Use either to read context + write back URLs. |
| `source` | `onboarding` = first-time creation. `regenerate` = re-generate from profile. `edit` = user gave custom instructions. |
| `composed_at` | Matches the row's `latest_generation_context.composed_at` at push time. Stale-trigger detection: if the row's value is newer, skip + delete. |
| `enqueued_at` | When the agent pushed the SQS message. For latency tracking. |

---

## `persons.latest_generation_context` JSONB (Postgres)

```json
{
  "prompt":            "<composed portrait prompt — pass directly to the image model>",
  "negative_prompt":   "<portrait negative — blocks photoreal of real living people + cartoon/Pixar look + deity iconography>",
  "mode":              "no_reference" | "with_reference",
  "reference_s3_key":  "<s3 key>" | null,
  "preset":            "<preset slug>" | null,
  "source":            "onboarding" | "regenerate" | "edit" | "auto",
  "composed_at":       "<ISO-8601 UTC>"
}
```

Populated by migration 0023 on existing rows; written fresh by the
agent on every new push.

---

## What Node needs to build

### 1. SQS worker (drains `flashback_agent-profile-picture`)

For each message:

1. **Parse** the trigger payload. Extract `record_id` (= `person_id`), `composed_at`.
2. **Read context from Postgres:**
   ```sql
   SELECT latest_generation_context
     FROM persons
    WHERE id = %s
   ```
   If `NULL` (or row missing), log + skip + delete.
3. **Stale check (recommended):** if `context.composed_at > message.composed_at`, a later push superseded this trigger — skip + delete.
4. **Generate the image:**
   - Call the image model with `context.prompt` and `context.negative_prompt`.
   - If `context.mode = 'with_reference'`, fetch `context.reference_s3_key` from S3 and supply as the reference / IP-adapter input.
   - Apply your `brand_default` model + LoRA + settings.
5. **Upload to S3:**
   - Full resolution → `profile-pictures/{person_id}/{job_id}.png` (or `.webp`).
   - Thumbnail (e.g. 256 × 256) → `profile-pictures/{person_id}/{job_id}_thumb.png`.
6. **Write back to Postgres** (`persons` table, row where `id = person_id`):
   - `image_url` — public or pre-signed URL for the full-res image.
   - `thumbnail_url` — URL for the thumbnail.
   - Do **not** touch `generation_prompt` or `latest_generation_context` — the agent owns those.
7. **Delete the SQS message** on success.

### 2. Error handling

- On image-model failure: dead-letter or retry up to 3×, then DLQ.
- On S3 upload failure: retry; do not write partial URLs.
- On Postgres write failure: retry; S3 upload is idempotent (overwrite by same key).
- The agent does not poll for completion. Node owns the full async lifecycle.

### 3. Idempotency note

`job_id` is unique per enqueue call (except `onboarding` where it equals `person_id`). If you replay a message, overwriting `image_url` / `thumbnail_url` with the new result is acceptable — the UI always reads the current value. With the `composed_at` stale-check, replays of older messages should be skipped automatically.

---

## Columns Node writes (persons table)

```sql
UPDATE persons
SET image_url      = '<full-res URL>',
    thumbnail_url  = '<thumb URL>'
WHERE id = '<person_id>';
```

These are the only two columns Node writes for this flow.

---

## Local development

The agent uses `PROFILE_PICTURE_QUEUE_URL=""` locally (empty string) to skip enqueuing. For end-to-end local testing, set:

```
PROFILE_PICTURE_QUEUE_URL=http://localhost:4566/000000000000/flashback_agent-profile-picture
```

...pointing at a LocalStack SQS instance. The agent still writes `latest_generation_context` to Postgres even when the queue URL is empty — so you can verify prompt composition locally without running the worker.
