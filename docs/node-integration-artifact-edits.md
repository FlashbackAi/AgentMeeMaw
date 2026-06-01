# Node Integration Brief — Artifact Edits, Regenerate, Presets, Reference Uploads

**For:** Node.js Backend team (or any assistant working on the Node side).
**Sister docs:** [backend-profile-picture-queue.md](backend-profile-picture-queue.md), [backend-artifact-edit-queue.md](backend-artifact-edit-queue.md). This brief consolidates both and adds Dynamo + UI guidance.
**Agent service:** Python FastAPI, `flashback.http.app:create_app`. All endpoints below are exposed here.

---

## 1. What shipped on the agent side

This rollout extends Flashback's image / video artifact flow with five changes — the last one is an architecture shift, the rest are additive:

1. **Painterly-realism (RDR2) style** for all artifacts (was Pixar/Studio Ghibli). Affects every newly generated portrait, moment, entity, and thread artifact. Negative prompts forbid cartoon/Pixar 3D look + deepfake likeness of real living people + deity / mythological iconography (Krishna-as-deity fix), but allow lifelike painterly depiction.
2. **Style/mood presets** (`painterly_cinematic` default + 4 variants). Available across all four record types. Surfaced via `GET /artifact-presets`.
3. **Edit stacking** for portraits + moments / entities / threads. Node sends cumulative `prior_instructions` (oldest first); the agent composes the prompt so every accepted edit is layered in order. Node persists per-record edit history in Dynamo.
4. **Reference-image uploads** for moments and entities (threads excluded). Node uploads to S3 and sends the key to the agent.
5. **Postgres-authoritative artifact-generation** (migration 0023). The agent now writes the full composed context (prompt + negative + mode + reference + preset + source + composed_at) to `<table>.latest_generation_context` on every push, **before** sending the SQS message. SQS messages are now **trigger-only** — Node's worker reads the prompt + everything else from Postgres at job processing time, not from the message body. This means Node is genuinely dumb — no prompt knowledge, no negative-prompt handling in code, just `SELECT latest_generation_context` and pass it through.

---

## 2. The preset registry

Five slugs. Default first. Slugs are part of the agent ↔ Node contract; labels and descriptions are user-facing and can be tuned by either side independently of the slug.

| slug | label | description | is_default |
|---|---|---|---|
| `painterly_cinematic` | Painterly cinematic | Default Flashback look — RDR2-style painterly realism with soft cinematic lighting. | true |
| `golden_hour` | Golden hour | Warm late-afternoon light with long soft shadows. | false |
| `twilight` | Twilight | Cool blue-hour light, rich shadows, warm window glow. | false |
| `storybook` | Storybook | Softer painterly brushwork, gentle storybook warmth. | false |
| `vintage_film` | Vintage film | Subtle film grain, faded color, 70s photochrome palette. | false |

`GET /artifact-presets` returns this list (modifier strings are internal — clients only see slug/label/description/is_default).

### Response shape

```json
{
  "presets": [
    {
      "slug": "painterly_cinematic",
      "label": "Painterly cinematic",
      "description": "...",
      "is_default": true
    },
    { "slug": "golden_hour", ... },
    ...
  ]
}
```

Cache this aggressively — the list changes rarely. On any unknown slug the agent returns 400, so always re-fetch if you see one.

---

## 3. Profile pictures (`record_type=person`)

The profile-picture path predates this rollout but gained `preset` + `prior_instructions`. It does **not** route through the generic `/artifacts/...` endpoints — `person` is intentionally excluded from those.

### 3.1 `POST /persons/{person_id}/profile-picture`  (regenerate)

```json
{
  "reference_s3_key": "uploads/<user>/<file>.jpg" | null,
  "preset": "golden_hour" | null
}
```

Response:

```json
{
  "job_id": "<fresh uuid>",
  "person_id": "<uuid>",
  "mode": "no_reference" | "with_reference",
  "source": "regenerate",
  "preset": "<resolved slug>",
  "enqueued": true | false
}
```

`preset: null` resolves to `painterly_cinematic`. Unknown slug → 400.

### 3.2 `POST /persons/{person_id}/profile-picture/edit`  (text edit)

```json
{
  "instructions": "give him round glasses",
  "prior_instructions": ["wearing a brown sherwani", "with a salt-and-pepper beard"],
  "reference_s3_key": "profile-pictures/<user>/<prev-job>.png" | null,
  "preset": "twilight" | null
}
```

- `instructions` — newest edit text. Required, 1–500 chars after trim.
- `prior_instructions` — cumulative history oldest first, ≤50 entries. Empty `[]` (or omitted) means no prior overlay.
- `reference_s3_key` — pass the previously generated image's S3 key to chain refinement, or a fresh upload to start from a new visual anchor, or null for text-only.
- `preset` — same as regenerate.

Response is the same shape as 3.1 but with `source: "edit"`.

### 3.3 What lands on `flashback_agent-profile-picture` SQS

```json
{
  "job_id": "<uuid>",
  "user_id": "<person_id>",
  "mode": "no_reference" | "with_reference",
  "reference_s3_key": "..." | null,
  "image_prompt": "<fully composed portrait prompt — pass directly to model>",
  "negative_prompt": "<fully composed scene+portrait negative>",
  "model_hints": {
    "preset": "brand_default",
    "guidance_scale": 7.5,
    "steps": 30,
    "seed": null
  },
  "raw_inputs": {
    "profile": { "display_name": "...", "gender": "...", "relationship": "..." },
    "user_prompt": "<newest edit text>" | null,
    "prior_user_prompts": ["<older edit 1>", "<older edit 2>", ...]
  },
  "source": "onboarding" | "regenerate" | "edit",
  "preset": "<slug>" | null,
  "enqueued_at": "ISO-8601"
}
```

`model_hints.preset` is the **model** preset (the agent always sends `"brand_default"` for now — your model + LoRA config). The **style** preset is the top-level `preset` field; use it for any UI badge / analytics tag, but you don't need to translate it into different model settings — the agent has already encoded its visual intent into `image_prompt`.

---

## 4. Generic artifacts (`record_type` ∈ {`moment`, `entity`, `thread`})

### 4.1 `POST /artifacts/{record_type}/{record_id}/regenerate`

```json
{
  "person_id": "<uuid>",
  "preset": "vintage_film" | null,
  "reference_s3_key": "uploads/<user>/house.jpg" | null
}
```

- `reference_s3_key` is **not allowed on threads** — the agent returns 400.
- `record_type=person` is **not routed here** — FastAPI rejects with 422. Use the profile-picture endpoint.

Response:

```json
{
  "job_id": "<uuid>",
  "record_type": "moment" | "entity" | "thread",
  "record_id": "<uuid>",
  "person_id": "<uuid>",
  "artifact_kind": "image" | "video",
  "mode": "no_reference" | "with_reference",
  "source": "regenerate",
  "preset": "<resolved slug>",
  "enqueued": true | false
}
```

`artifact_kind` is derived from `record_type` (moment → `video`, entity → `image`, thread → `image`).

### 4.2 `POST /artifacts/{record_type}/{record_id}/edit`

```json
{
  "person_id": "<uuid>",
  "instructions": "more snow on the ground",
  "prior_instructions": ["add a red truck", "warmer porch light"],
  "reference_s3_key": "uploads/<user>/actual-house.jpg" | null,
  "preset": "twilight" | null
}
```

Same validation rules as 4.1 + the regular field constraints from §3.2. Response is the same shape as 4.1 with `source: "edit"`.

### 4.3 What lands on `flashback_agent-artifact-generation` SQS

The full payload (extended from the legacy 5-field shape):

```json
{
  "record_type":       "moment" | "thread" | "entity",
  "record_id":         "<uuid>",
  "person_id":         "<uuid>",
  "artifact_kind":     "image" | "video",
  "generation_prompt": "<fully composed prompt — pass directly to model>",
  "negative_prompt":   "<scene-art negative>" | null,
  "mode":              "no_reference" | "with_reference",
  "reference_s3_key":  "<s3 key>" | null,
  "source":            "auto" | "regenerate" | "edit",
  "preset":            "<slug>" | null,
  "raw_inputs": {
    "user_prompt":        "<newest edit text>" | null,
    "prior_user_prompts": ["<older edit 1>", ...]
  },
  "enqueued_at": "ISO-8601"
}
```

**Backward compatibility:** The extraction worker still pushes the legacy 5-field shape augmented with defaults — `source="auto"`, `mode="no_reference"`, `reference_s3_key=null`, `preset=null`, `negative_prompt=null`, `raw_inputs={user_prompt:null, prior_user_prompts:[]}`. Existing worker code reading only the original 5 fields keeps working. To pick up new behavior (apply the scene negative, accept references, log the preset), read the new fields.

`negative_prompt=null` on `source=auto` means the extraction LLM's emitted prompt already carries the "no faces, no photorealism, painterly" guidance inline — apply your model-side default negative or none.

---

## 5. Reference-image upload flow

UI affordance:

- Profile picture card → "Upload reference" button → file picker.
- Moment / entity card → "Use a reference photo" option inside the regenerate or edit modal.
- Thread card → no reference-upload affordance (would be rejected by the agent).

Implementation steps Node owns:

1. Accept the upload (size cap recommended ≤ 10 MB, types JPG/PNG/WebP). Reject obviously non-photo content client-side.
2. Upload to S3 under `uploads/{user_id}/{record_type}/{record_id}/{ts}.{ext}` (or whatever shape your S3 layout prefers). Persist the resulting key.
3. Send the key to the agent on the regenerate / edit call as `reference_s3_key`.
4. Save it in Dynamo (§7) as `last_reference_s3_key` so subsequent edits can default to chaining off it.
5. Your existing SQS worker already knows how to fetch a `reference_s3_key` from S3 and feed it to the image model — the agent simply forwards the key it received.

---

## 6. Edit stacking semantics

**The agent is stateless.** Edit history lives entirely on Node. Every `/edit` call must include the full cumulative `prior_instructions` list (oldest first) — the agent does not remember previous edits.

The agent composes the prompt as:

```
base_recipe + prior_instructions[0] + prior_instructions[1] + ... + instructions + preset_modifier
```

So the order Node sends decides the order in the final prompt. Always append the newest edit at the end of `prior_instructions` after the agent enqueues successfully (see §7).

When the user wants to "start fresh" (drop overlays):

- Call `/regenerate` (it doesn't read `prior_instructions`). Clear the Dynamo `prior_instructions` array if you want the next `/edit` call to start clean too.
- Or call `/edit` with `prior_instructions: []` and the new text. The agent will compose `base + instructions` only.

---

## 7. Dynamo edit-history table

Recommended shape (one shared table, keyed by record_type + record_id):

```
table: flashback_artifact_edits
PK: { record_type: string, record_id: string }      # composite or two attributes
attributes:
  person_id:                 string
  prior_instructions:        list<string>           # oldest first
  last_reference_s3_key:     string?                # most recent uploaded reference
  last_preset:               string?                # most recent preset slug
  updated_at:                iso-8601
  source_of_last_update:     "regenerate" | "edit"
```

Lifecycle per call:

| Trigger | Read | Write after agent 2xx |
|---|---|---|
| User opens edit modal | Read `prior_instructions`, `last_reference_s3_key`, `last_preset` to populate UI. | — |
| User submits `/edit` | Send `prior_instructions` from Dynamo, plus the new `instructions`. | Append the newest instruction; update `last_reference_s3_key`, `last_preset`, `updated_at`, `source_of_last_update="edit"`. |
| User submits `/regenerate` | Read `last_preset` / `last_reference_s3_key` to pre-fill the picker (optional). | Update `last_preset`, `last_reference_s3_key`, `updated_at`, `source_of_last_update="regenerate"`. Do **not** touch `prior_instructions` — regenerates are stylistic re-rolls, not history resets. |
| User clicks "Reset edits" | — | Clear `prior_instructions` to `[]`. |

**Failure handling:** if the agent call returns non-2xx, do NOT append to `prior_instructions` — keep the Dynamo state aligned with what was actually enqueued. If you append optimistically and later need to roll back, you'll lose track of what the model was told.

**Profile picture edit history** uses the same table with `record_type="person"`, `record_id=person_id`.

---

## 8. UI affordances — concrete shapes

### 8.1 Regenerate ▾ picker

Wherever a "Regenerate" button exists today, replace it with a split control: primary button + dropdown.

- Primary button (`Regenerate`) → calls `/regenerate` with no preset (defaults to `painterly_cinematic`).
- Dropdown → opens a menu listing presets from `GET /artifact-presets`. Default is shown with a subtle "Default" tag; user picks one → calls `/regenerate` with `preset=<slug>`.
- Below the menu (for moments + entities only): "Use a reference photo →" link that opens the upload modal.

### 8.2 Edit modal

Single freeform input, multiline, max 500 chars.

- Above the input: a small list of prior edits (chips or stacked lines) showing the current `prior_instructions` from Dynamo. Each has a "✕" to remove (which mutates the Dynamo array on save). The user sees what's already layered.
- Below the input: optional preset picker (same five slugs) + reference-image upload affordance (for moment/entity only).
- "Save" → `/edit` with the full `prior_instructions` (after any user removals) + the new `instructions` + chosen preset + reference key.

### 8.3 "Start fresh" affordance

A "Reset edits" link on the edit modal, separate from the close action. Clicking it sets `prior_instructions: []` in Dynamo (after a confirmation) and pre-clears the chip list. The next regenerate or edit starts clean.

---

## 9. Style consistency — what Node should NOT do

**Single rule: Postgres is authoritative; the agent composes + writes,
Node reads + passes through.**

For every artifact-generation job, the agent composes the full context
(base scene description / portrait recipe + cumulative `prior_instructions`
+ newest `instructions` + preset modifier + negative prompt + reference
key) and writes it to `<table>.latest_generation_context` BEFORE pushing
the SQS message. The SQS message is a **trigger only** — it carries job
identifiers, not prompt content. Node:

- ✅ Reads the trigger payload to figure out *which row* needs an artifact (`record_type`, `record_id`, `composed_at`).
- ✅ Reads `<table>.latest_generation_context` from Postgres at job processing time. That's where `prompt`, `negative_prompt`, `mode`, `reference_s3_key`, and `preset` live.
- ✅ Uses `context.negative_prompt` when present; falls back to your model-side default only when it's null (extraction-time `source=auto` pushes have null negatives — the LLM-emitted prompt carries the style guidance inline).
- ✅ Honors the `composed_at` stale-check: if `context.composed_at > message.composed_at`, a later push superseded this trigger — skip + DeleteMessage.
- ❌ Does NOT read `generation_prompt` from the row's top-level column for the prompt-to-send. That column holds only the LLM-emitted base scene description; the composed-with-edits-and-preset version is in `latest_generation_context.prompt`.
- ❌ Does NOT prepend / append style instructions to the prompt before sending to the model — this defeats the preset system.
- ❌ Does NOT translate the `preset` slug into model-side LoRA / settings changes unless coordinated with the agent team. Today it's purely a label + a prompt modifier on the agent side.
- ❌ Does NOT modify `latest_generation_context` or `generation_prompt` on any artifact-bearing row — only Node's SQS worker writes the URL columns (`image_url`, `video_url`, `thumbnail_url`) on completion.

---

## 10. Local development

The agent reads queue URLs from env vars:

```
PROFILE_PICTURE_QUEUE_URL=""                 # empty = skip enqueue, returns enqueued: false
ARTIFACT_QUEUE_URL=""                        # same
```

Local end-to-end with LocalStack SQS:

```
PROFILE_PICTURE_QUEUE_URL=http://localhost:4566/000000000000/flashback_agent-profile-picture
ARTIFACT_QUEUE_URL=http://localhost:4566/000000000000/flashback_agent-artifact-generation
```

The agent endpoints still return 200 with `enqueued: false` if the queue URL is empty — useful for prompt-shape testing without a worker.

---

## 11. Acceptance checklist for the Node side

When this lands on Node, verify:

**Infrastructure (do these first — UI work depends on them):**

- [ ] **Create DynamoDB table `flashback_artifact_edits`** with PK = `(record_type: string, record_id: string)`. Attributes: `person_id`, `prior_instructions: list<string>`, `last_reference_s3_key: string?`, `last_preset: string?`, `updated_at: iso-8601`, `source_of_last_update: "regenerate" | "edit"`. Same table covers `record_type ∈ {person, moment, entity, thread}` — no separate tables per type. See [§7](#7-dynamo-edit-history-table) for the lifecycle.
- [ ] Existing S3 bucket has a route for `uploads/{user_id}/{record_type}/{record_id}/{ts}.{ext}` — no new bucket needed; just confirm the path layout is supported and that the worker has read access.
- [ ] **No new Postgres tables required.** The agent owns the canonical graph; do not add columns to `persons` / `moments` / `entities` / `threads` for edit history — that's the Dynamo table's job.

**Wiring:**

- [ ] `GET /artifact-presets` is called on app boot or first artifact-modal open; the list is cached and re-fetched on 24h TTL.
- [ ] Profile-picture edit modal sends `prior_instructions` from the Dynamo row on every call.
- [ ] Profile-picture edit modal allows preset selection and reference-image upload.
- [ ] Moment / entity / thread cards expose a regenerate ▾ picker with the preset list.
- [ ] Moment / entity cards expose reference-image upload; thread cards do NOT.
- [ ] Dynamo `prior_instructions` is appended only after the agent returns 2xx.
- [ ] `prior_instructions` is not reset on regenerate; only on explicit "Reset edits".
- [ ] SQS worker treats the message as a trigger only — extracts `record_type`, `record_id`, `composed_at` and ignores any other fields.
- [ ] SQS worker reads `<table>.latest_generation_context` from Postgres for the prompt + negative + mode + reference + preset.
- [ ] SQS worker performs the `composed_at` stale-check: if the row's `composed_at` is newer than the message's, skip + DeleteMessage.
- [ ] SQS worker falls back to its model-side default negative only when `context.negative_prompt IS NULL` (extraction-time auto pushes).
- [ ] SQS worker fetches the reference image from S3 when `context.mode = 'with_reference'`.
- [ ] SQS worker writes the URL columns (`image_url` / `video_url` / `thumbnail_url`) back to Postgres on success; nothing else.
- [ ] No prompt mutation on the Node side before passing to the model.
- [ ] No reads of `generation_prompt` from the top-level column for the prompt-to-send — that column is the immutable LLM-emitted base, not the composed prompt.

---

## 12. Known issues / mitigations

### 12.1 Deity / cultural-name collisions in portraits — **mitigated**

Submitting a `persons.name="Krishna"` used to yield a Hindu-deity rendering (peacock feather, flute, blue skin) instead of an ordinary person named Krishna. Same risk for other names with strong iconographic priors (Jesus, Buddha, Ganesh, Apollo, Athena, etc.). The portrait prompt has been hardened against this on the agent side:

- **Real-person anchor** — every portrait prompt now opens with `"an ordinary contemporary person living a normal everyday life, not a religious or mythological figure, modern real-world setting"` immediately after the name, before name-driven priors can bias the composition.
- **Relationship anchor** — the relationship clause now reads `"depicted as an ordinary contemporary {relationship}, modern everyday clothing"` instead of the previous bare `"depicted as a {relationship}"`.
- **Negative-prompt additions** — `NEGATIVE_PROMPT` now blocks the strong deity tells: `religious deity, god, goddess, divine being, mythological figure, holy avatar, sacred icon, halo, divine aura, glowing aureole, celestial light, multi-armed figure, multiple arms, blue-skinned deity, green-skinned deity, gold-skinned deity, peacock-feather crown, deity holding a flute, lotus throne, religious altar backdrop, temple sanctum backdrop`.
- Ordinary cultural attire (tilaka, sari, kurta, kippah, hijab, cross-necklace, etc.) is deliberately NOT in the negative — those belong on ordinary people of any faith and stay allowed. Regression tests pin this.

**Where the prompt lives:** [src/flashback/profile_picture/prompt.py](../src/flashback/profile_picture/prompt.py) — `compose_image_prompt()` + `NEGATIVE_PROMPT`. Regression tests at [tests/profile_picture/test_prompt.py](../tests/profile_picture/test_prompt.py).

**If a deity rendering still slips through**, the prompt fix is probabilistic — escalation options (in order of cost):

1. Add the specific failing name to a small `_DEITY_NAME_PRIORS` set in `prompt.py` and append a name-targeted reinforcement (e.g. `"a man named Krishna, distinct from the Hindu god of the same name"`) when matched.
2. Strengthen `model_hints.negative_prompt_weight` on the Node side for portrait jobs.
3. Move portrait generation to a different base model or LoRA that has weaker mythological priors.

Coordinate with the agent team before any of these — the prompt sits inside the same file as the rest of the portrait recipe and any change should land with regression tests.

### 12.2 LLM-driven node_edits collapse on short refinements

Fixed on the agent side as of `node_edits.moment.v2` / `node_edits.entity.v2` (merge-first prompt). Node doesn't need to change anything for this. If you observe a moment or entity description collapsing to just the new edit text, please file a bug with the agent team — that's a regression of the v2 prompt.

---

## 13. Cross-references

- Profile-picture queue contract: [docs/backend-profile-picture-queue.md](backend-profile-picture-queue.md)
- Artifact-generation queue contract: [docs/backend-artifact-edit-queue.md](backend-artifact-edit-queue.md)
- Repo-level boundaries (what the agent owns vs. what Node owns): [CLAUDE.md §3](../CLAUDE.md)
- API surface: [CLAUDE.md §9](../CLAUDE.md)
- Visual register / product constraints (no photoreal subjects in v1): [CLAUDE.md §1](../CLAUDE.md), [LEGACY_MODE_BRIEF.md](../LEGACY_MODE_BRIEF.md)

---

## 14. Quick prompt for an AI assistant doing the Node-side work

Copy/paste into a fresh assistant session if you want a one-shot brief:

> You are implementing the Node.js Backend side of Flashback's artifact edit / regenerate / preset rollout. The Python agent owns prompt composition + writes it to Postgres; Node is a **dumb** worker — it reads a trigger from SQS, reads the composed context from Postgres, generates the image / video, writes the URL back. Node has zero prompt logic.
>
> Agent HTTP endpoints (already shipped):
>
> - `GET /artifact-presets` — list of style presets (5 slugs: `painterly_cinematic` default, `golden_hour`, `twilight`, `storybook`, `vintage_film`). Cache 24h.
> - `POST /persons/{person_id}/profile-picture` and `/edit` — portrait regenerate / edit; both accept `preset` and `reference_s3_key`; `/edit` also accepts `prior_instructions: list[string]`.
> - `POST /artifacts/{record_type}/{record_id}/regenerate` and `/edit` for `record_type ∈ {moment, entity, thread}`. Same shape. Threads reject `reference_s3_key`.
>
> Two SQS queues to drain — both carry **trigger-only payloads**:
>
> ```json
> // flashback_agent-artifact-generation (moments / entities / threads)
> {
>   "job_id": "uuid",
>   "record_type": "moment" | "entity" | "thread",
>   "record_id": "uuid",
>   "person_id": "uuid",
>   "artifact_kind": "image" | "video",
>   "source": "auto" | "regenerate" | "edit",
>   "composed_at": "iso-8601",
>   "enqueued_at": "iso-8601"
> }
> ```
>
> ```json
> // flashback_agent-profile-picture (portraits)
> {
>   "job_id": "uuid",
>   "record_type": "person",
>   "record_id": "person_id",
>   "person_id": "person_id",
>   "artifact_kind": "image",
>   "source": "onboarding" | "regenerate" | "edit",
>   "composed_at": "iso-8601",
>   "enqueued_at": "iso-8601"
> }
> ```
>
> Prompt + negative + mode + reference + preset live in Postgres on `<table>.latest_generation_context` (JSONB):
>
> ```json
> {
>   "prompt": "<composed — pass to model>",
>   "negative_prompt": "<scene/portrait negative>" | null,
>   "mode": "no_reference" | "with_reference",
>   "reference_s3_key": "<s3 key>" | null,
>   "preset": "<slug>" | null,
>   "source": "auto|onboarding|regenerate|edit",
>   "composed_at": "iso-8601"
> }
> ```
>
> Your job on Node:
>
> 1. Create a Dynamo table `flashback_artifact_edits` keyed by `(record_type, record_id)` storing `prior_instructions: list<string>`, `last_reference_s3_key`, `last_preset`, `updated_at`. Same table covers `record_type="person"`.
> 2. Wire reference-image upload (S3 under `uploads/{user_id}/{record_type}/{record_id}/{ts}.{ext}`) on profile / moment / entity cards; gated off for threads.
> 3. Update both SQS workers: parse the trigger, `SELECT latest_generation_context FROM <table> WHERE id = %s`, check the `composed_at` for staleness (skip + DeleteMessage if the row's value is newer than the message's), then generate with `context.prompt` + `context.negative_prompt` (fall back to your default if null) + reference from S3 if `context.mode='with_reference'`. Write `image_url` / `video_url` / `thumbnail_url` back to Postgres on success. Do NOT touch `generation_prompt` or `latest_generation_context`.
> 4. Frontend: replace each "Regenerate" button with a split control whose dropdown lists presets from `/artifact-presets`. Add an edit modal that shows the current `prior_instructions` from Dynamo as chips, accepts a new edit, lets the user pick a preset and optionally upload a reference photo.
> 5. After every successful `/edit` call, append the new instruction to Dynamo. After `/regenerate`, only update `last_preset` / `last_reference_s3_key` — never touch `prior_instructions`. On explicit "Reset edits", clear `prior_instructions` to `[]`.
>
> Honor the §3 boundaries from the agent's CLAUDE.md: Node writes only the URL columns. Node never composes prompts or reads `generation_prompt` from the top-level column for the prompt-to-send (that's the immutable LLM-emitted base; the composed version is in `latest_generation_context`).
>
> The full brief is at `docs/node-integration-artifact-edits.md` in the agent repo. Cross-reference for the full response shapes, acceptance checklist (§11 — starts with "Create the Dynamo table"), and known issues (§12 — deity-name collisions on portraits, mitigated agent-side; merge-first node_edits also mitigated agent-side).
