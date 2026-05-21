# Backend Services: Profile-Picture Generation Queue

**For:** Node.js Backend team  
**Queue name:** `flashback_agent-profile-picture`  
**Queue URL:** `https://sqs.ap-south-1.amazonaws.com/768699754860/flashback_agent-profile-picture`  
**Queue type:** Standard SQS (not FIFO)

---

## What the agent pushes

The Python agent pushes a JSON message to this queue whenever it wants Node to generate and store a profile picture for a person. The agent composes the full `image_prompt` before enqueuing — the backend does not need to build any prompt logic.

### Message schema

```json
{
  "job_id": "string (UUID)",
  "user_id": "string (UUID — same as person_id in Postgres)",
  "mode": "no_reference | with_reference",
  "reference_s3_key": "string | null",
  "image_prompt": "string — full ready-to-send prompt",
  "negative_prompt": "string",
  "model_hints": {
    "preset": "brand_default",
    "guidance_scale": 7.5,
    "steps": 30,
    "seed": null
  },
  "raw_inputs": {
    "profile": {
      "display_name": "string",
      "gender": "male | female | non_binary | unspecified",
      "relationship": "string | null"
    },
    "user_prompt": "string | null"
  },
  "source": "onboarding | regenerate | edit",
  "enqueued_at": "ISO-8601 UTC timestamp"
}
```

### Field notes

| Field | Notes |
|---|---|
| `job_id` | For `source=onboarding` this is the stable `person_id` (one job per person creation). For `regenerate`/`edit` it is a fresh UUID each call — allows multiple jobs per person. |
| `user_id` | Always the Postgres `persons.id` UUID. Use this to write `image_url` / `thumbnail_url` back. |
| `mode` | `no_reference` = text-to-image only. `with_reference` = `reference_s3_key` is set; use it as the reference/IP-adapter input. |
| `reference_s3_key` | S3 key of the user-uploaded reference photo. `null` when `mode=no_reference`. |
| `image_prompt` | Pixar-style stylized portrait prompt, fully composed. Pass directly to the image model. |
| `negative_prompt` | Pass directly to the model as the negative conditioning string. |
| `model_hints.preset` | `"brand_default"` — apply your brand-default model + LoRA config. |
| `raw_inputs` | Agent's original inputs before prompt composition. Useful for logging / reprocessing; do not need to be displayed to users. |
| `source` | `onboarding` = first-time creation. `regenerate` = re-generate from profile. `edit` = user gave custom instructions (see `raw_inputs.user_prompt`). |

---

## What Node needs to build

### 1. SQS worker (drains `flashback_agent-profile-picture`)

For each message:

1. **Parse** the JSON payload and validate `job_id`, `user_id`, `mode`.
2. **Generate the image:**
   - Call the image model with `image_prompt` and `negative_prompt`.
   - If `mode=with_reference`, fetch `reference_s3_key` from S3 and supply as the reference input.
   - Apply `model_hints.preset` — map `"brand_default"` to your configured model + LoRA + settings.
3. **Upload to S3:**
   - Full resolution → `profile-pictures/{user_id}/{job_id}.png` (or `.webp`).
   - Thumbnail (e.g. 256 × 256) → `profile-pictures/{user_id}/{job_id}_thumb.png`.
4. **Write back to Postgres** (`persons` table, row where `id = user_id`):
   - `image_url` — public or pre-signed URL for the full-res image.
   - `thumbnail_url` — URL for the thumbnail.
   - Do **not** touch `generation_prompt` — the agent already owns that column for other artifact types.
5. **Delete the SQS message** on success.

### 2. Error handling

- On image-model failure: dead-letter or retry up to 3×, then DLQ.
- On S3 upload failure: retry; do not write partial URLs.
- On Postgres write failure: retry; S3 upload is idempotent (overwrite by same key).
- The agent does not poll for completion. Node owns the full async lifecycle.

### 3. Idempotency note

`job_id` is unique per enqueue call (except `onboarding` where it equals `person_id`). If you replay a message, overwriting `image_url` / `thumbnail_url` with the new result is acceptable — the UI always reads the current value.

---

## Columns Node writes (persons table)

```sql
UPDATE persons
SET image_url      = '<full-res URL>',
    thumbnail_url  = '<thumb URL>'
WHERE id = '<user_id>';
```

These are the only two columns Node writes for this flow. The agent owns everything else on the `persons` row.

---

## Local development

The agent uses `PROFILE_PICTURE_QUEUE_URL=""` locally (empty string) to skip enqueuing. For end-to-end local testing, set:

```
PROFILE_PICTURE_QUEUE_URL=http://localhost:4566/000000000000/flashback_agent-profile-picture
```

...pointing at a LocalStack SQS instance.
