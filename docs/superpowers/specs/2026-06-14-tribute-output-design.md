# Tribute Output (Father's Day launch) — Design

**Date:** 2026-06-14
**Status:** Approved design, pre-implementation
**Author:** brainstormed with Claude Code

---

## 1. Summary

A new **Tribute** capability: a contributor records a heartfelt, shareable
output about the subject of a legacy. Two outputs:

- **Tribute video** (the hero, shareable on social) — a painterly-realism
  montage of shared memories that climaxes in the contributor's direct
  message to the subject (the "I love you they never said").
- **Storybook** — a compiled book of the subject's life.

The capability is **general and reusable for any subject** (mother, mentor,
friend). **Father's Day is a campaign *skin*** — copy + a "featured"
placement flag layered on top, not a separate feature. The neutral tribute
flow lives on year-round after the campaign.

The whole feature is **additive**. The normal turn/segment/extraction/themes
pipeline is unchanged.

### Product framing (the "why")

Today the product is an **archivist**: it builds the *subject's* memory
graph and deliberately keeps the *contributor* out (trait descriptions
exclude the speaker; no impersonation). The tribute video intentionally
**flips the lens** — it is the *contributor's* message, in the
contributor's voice. This is a new output mode, not a change to the
archive. The motivating insight: users pour stories about their father
into the product and get *nothing back* to hold or share. The tribute is
the outcome — good enough that they want to post it. That post is the
launch.

### Decisions locked during brainstorming

| Decision | Choice |
|---|---|
| Video point-of-view | Contributor's tribute (their message TO the subject) |
| Launch scope | Both video + storybook, **video-first**; additive only |
| Reuse | General tribute capability; **Father's Day = skin** |
| Completion model | **Fixed ingredient checklist** (→ honest "what's left") |
| Message wording | **LLM-polished from the user's own words** |
| Storybook reach | **General output** any legacy can produce; tribute flow feeds a curated variant |
| Read surface | **SQL view, read directly by Node** (no GET endpoints) |

---

## 2. Scope & non-goals

**In scope**
- A `tribute` theme kind reusing the existing themes/archetype/unlock machinery.
- A new `tributes` table holding the contributor message + assembled script +
  checklist snapshot + compiled-artifact rows.
- A completion checklist (config in code) + a status **view** Node reads.
- The `message` capture lane (structured sidecar, never extracted).
- Compiled-artifact composition + new `artifact_generation` job kinds
  (`tribute_video`, `storybook`).
- A Father's Day campaign skin (copy + featured flag + expanded question set).
- The **storybook** as a general compiled output usable by any legacy.

**Visual register (applies to BOTH video scenes and storybook pages)**
- **Painterly-realism only, in the Red Dead Redemption 2 register** —
  naturalistic features + lighting with painterly brushwork. **No
  photorealism.** Photoreal/deepfake likeness of real living people is
  negative-prompted on every composed scene/page prompt, per CLAUDE.md §1.

**Non-goals (v1)**
- Voice cloning / narration in the subject's voice (negative-prompted, per
  product rules). The video uses text-on-screen + the polished message;
  optional contributor voiceover is a Node/frontend concern.
- Photoreal/deepfake likeness of living people.
- Auth (Node remains the boundary).
- The agent rendering or storing media. **Node renders, uploads to S3, and
  writes all URL columns.** We only compose prompts + push jobs.

---

## 3. Output split (who gets what)

| | Enters tribute / FD flow | Normal user, never enters it |
|---|---|---|
| **Tribute video** | ✅ message + curated scenes | ❌ (no message captured) |
| **Storybook** | ✅ tribute-flavored (curated around the message) | ✅ plain legacy storybook from their graph |
| Normal moment/entity/thread artifacts | ✅ unchanged | ✅ unchanged |

- **Tribute video** intrinsically needs the contributor `message`; it is a
  tribute-flow-only output.
- **Storybook** is a **single general compiled-artifact engine**
  (`record_type='storybook'`) that reads the graph. The tribute flow feeds it
  a curated scene order + a message page; a normal user generates a plain
  storybook from accumulated memories (a retention hook beyond the campaign).

---

## 4. Data model (migration 0027)

### `tributes` table

One row per tribute output per person (not 1:1 — a contributor may make more
than one over time).

| column | purpose |
|---|---|
| `id`, `person_id`, `theme_id` | identity + link to the tribute theme |
| `message_text` | the **polished** contributor message (new capture lane) |
| `message_source_turns` | raw user words it was distilled from (provenance) |
| `script` JSONB | ordered scenes + captions + message placement |
| `scene_moment_ids` UUID[] | which moments became scenes |
| `checklist_state` JSONB | filled/unfilled snapshot at assembly time |
| `status` | `draft → ready → generating → complete` |
| `video_url`, `image_url`, `thumbnail_url` | **Node writes** |
| `generation_prompt`, `latest_generation_context` JSONB | **we write** (per §3 of CLAUDE.md) |
| `created_at`, `updated_at`, `status` discipline | standard |

**Naming is campaign-neutral.** The column is `message_text`, the checklist
slot is `message`. The user-facing word ("confession" on Father's Day,
"tribute"/"dedication" elsewhere) is **skin-supplied copy**, never baked into
schema. The new artifact `record_type` is `tribute`, never `father`.

### Theme seeding

- Add a `tribute` **theme kind** alongside `universal` / `emergent`.
- **Not** seeded for every legacy at creation — seeded/unlocked **on demand**
  when the user enters the tribute flow, keeping normal legacies clean.
- The expanded archetype question set lives on the theme row exactly like
  today (cached JSONB), just a larger count.

### Checklist (config in code: `flashback/tribute/checklist.py`)

The checklist lives in code (tunable without migration). Each slot has a
`key`, label, weight, and a probe. **Required slots** (gate the video):

| slot | filled when | reads from |
|---|---|---|
| `memories` (needs 3) | ≥3 active moments themed to the tribute, each with `sensory_details` or an `involves` edge | graph (existing) |
| `message` | `message_text` present on the tribute row | tribute row (new) |
| `appearance` | `ground_truth` has region + (birth_era or era_span) + one of distinctive_features/attire/build | ground_truth (existing) |
| `signature` | ≥1 active trait, OR an entity with a `saying`/`mannerism` attr | graph (existing) |

**Weighted percent:** memories 40 / message 30 / appearance 20 / signature 10.
**100% = "video ready to generate."** The only genuinely new state is
`message_text`; every other slot reads the existing graph.

---

## 5. Capture flow — where the message is asked

The message is the most vulnerable ask, so placement follows the product's
emotional-pacing rules: **never cold, never an MC chip.** It is a deliberate
**late beat in the steered chat**, modeled mechanically on the existing
GT-tap / `ground_truth_answer` pattern (invariant #26) — **structured, never
mined into moments.**

Flow inside a tribute/FD session:

1. **Expanded archetype MC (unlock)** — warms up; fills `appearance`, seeds
   `signature` / `memories` priors. 6–8 questions vs the normal 3–4, via the
   same `generate_archetype_questions` machinery + the skin's framing context.
2. **Steered chat** — the agent biases questions/taps toward unfilled
   checklist slots (see §6); memories flow through normal extraction.
3. **Message invitation fires** when *all three* hold:
   - other required slots mostly filled (memories ≥ ~2, appearance present),
   - emotional temperature is high (Intent Classifier output — the same gate
     GT taps use),
   - it has not already been asked this session.

   The agent emits a signposted card; copy comes from the skin. FD:
   *"Fathers and sons don't always say it out loud. If he could hear one
   thing from you right now — what is it?"* Neutral: *"If you could say one
   thing straight to them, what would it be?"*
4. **Structured capture (not chat):** the card has a free-text box (+ optional
   gentle scaffolds). The answer returns on `/turn` as a new **`message_answer`**
   sidecar field → written to `message_source_turns` → a small LLM polishes it
   into `message_text`. **Because it rides the sidecar, extraction never sees
   it** — the message stays out of the memory graph, exactly like GT answers.

**Mechanics reused:** a `signal_pending_message` flag in Working Memory
(mirrors `signal_pending_tap_question`) tells the next turn's route/classifier
the incoming sidecar is the message. **Freeze/skip:** the slot stays unfilled
— the **video** stays gated, the **storybook** can still generate. The agent
may re-invite once in a later session, never nagging.

---

## 6. Steering

The agent steers toward unfilled checklist slots, reusing existing ranking:

- The producer/tap ranker is biased toward candidates whose themes/dimensions
  map to unfilled slots (analogous to `THEME_BIAS_WEIGHT` soft bias —
  never a hard filter; the user can drift and the agent follows).
- `appearance` gaps surface as the existing **ground-truth taps**.
- `memories` / `signature` gaps bias question selection toward those areas.
- Steering is **soft**; the conversation stays natural, matching the product's
  "never a survey" rule.

---

## 7. Completion meter + API surface

### Read surface — a SQL view, read directly by Node

Consistent with how Node consumes `active_themes_with_tier`, tribute status is
exposed as a **view** (`tribute_status`), not a GET endpoint:

- Derives the checklist slots + weighted percent from `themed_as` ×
  `active_moments`, `ground_truth`, traits/entities, and the `message_text`
  column.
- Returns: `percent`, `ready`, per-slot `{key, label, filled, hint}`, the
  campaign `featured` flag + `active_window`, and the final `video_url` /
  storybook URLs once Node writes them.
- The checklist **weights/labels** live in code for assembly logic; the
  **filled/percent computation is in the view** so Node gets it for free.
- If a probe is too complex for pure SQL, the view computes the countable
  slots and the agent stamps `message` presence — still no GET endpoint.

### Live meter during a session

`/turn` metadata **echoes `tribute_progress`** so the meter ticks up
mid-session without Node re-querying every turn. Extraction is async, so
graph-backed slots (memories, signature) flip only **after extraction
commits** — Node refreshes the meter on the existing **`extraction_complete`
NOTIFY** (invariant #25). The meter is **monotonic within a tribute** (never
regresses confusingly).

### HTTP surface — POST-only actions (all unauthed; Node is the boundary)

- `POST /tributes/{id}/generate` — assemble + push compiled jobs. **Video**
  gated on `ready=true`; **storybook** allowed once it meets a minimum page
  count.
- Message capture needs **no** endpoint — it arrives on `/turn` via the
  `message_answer` sidecar.
- Theme unlock / session start already exist and are reused.

Everything the UI **reads** (progress, slots, "what's left," final URLs) Node
reads straight from the view/row in Postgres.

---

## 8. Assembly + Node boundary

On `POST /tributes/{id}/generate`:

1. **Assemble script** (big LLM, `claude-sonnet-4-6`): select + order the
   scene moments, write captions, place `message_text` as the emotional
   climax, choose open/close. Stored in `tributes.script`.
2. **Compose two compiled contexts**, written to `latest_generation_context`
   on the tribute row **before** pushing (per CLAUDE.md §3):
   - **video** → `record_type='tribute'`, `artifact_kind='tribute_video'`:
     `{scenes:[{prompt, negative, reference_s3_key?, duration_seconds}],
     message_text, captions, order, style_preset (RDR2 painterly),
     target_duration_seconds, composed_at}`.
     `generation_prompt` retains the immutable base scene descriptions.
     **Video length:** `target_duration_seconds` comes from the skin config
     (default **45s**, social-friendly range 30–60s). Assembly bounds the
     scene count and per-scene `duration_seconds` so the sum lands on the
     target; Node's renderer honors it. Length is a **contract field** the
     compiled-renderer must respect (see §8 dependency).
   - **storybook** → `record_type='storybook'`, `artifact_kind='storybook'`:
     `{pages:[...], cover, composed_at}`. **Hard cap: 9 pages max** (cover +
     up to 8 content pages, or 9 content pages — finalize during
     implementation); assembly truncates/curates the strongest moments to fit.
   - Every scene/page `prompt` carries the painterly-realism style preset and
     the photoreal/deepfake **negative prompt** (see Visual register, §2).
3. **Push `artifact_generation` jobs** carrying identifiers only: `job_id`,
   `record_type`, `record_id` (= tribute id), `person_id`, `artifact_kind`,
   `source`, `composed_at`.
4. **Node renders** the compiled job, uploads to S3, writes
   `video_url` / `image_url` / `thumbnail_url` back. UI observes URL presence
   via the view; status flips `generating → complete`.

### ⚠️ Critical-path cross-repo dependency

Today's `artifact_generation` jobs are **per-record single artifacts**. A
**compiled multi-scene video** is a new job shape Node's worker does not
handle yet. The Node team must build a compiled-job renderer in the **other
repo**. Our side can be complete, but the video will not render until Node
ships its half. **Confirm Node's capacity before committing to the Father's
Day date.** This is the single biggest risk to the 8-day runway.

---

## 9. Father's Day skin

Pure config + copy, no logic fork. A campaign descriptor
(`flashback/tribute/campaigns.py`):

```
{ slug: "fathers_day_2026",
  theme_display_name: "A Letter to Dad",
  message_card_copy: "Fathers and sons don't always say it out loud...",
  archetype_extra_context: "<framing for the expanded Q set>",
  featured: true,
  video_target_seconds: 45,
  active_window: [2026-06-14, 2026-06-22] }
```

- A **default neutral skin** is always available year-round (general tribute).
  The FD skin overrides copy + sets `featured`.
- **Expanded questions:** 6–8 archetype questions vs the normal 3–4 — same
  machinery, higher count, skin framing. Cached on the theme row.
- **"First thing in UX"** is **Node's** placement decision (we don't own
  frontend). We expose the tribute theme + `featured` / `active_window`
  through the view; Node features it during the window.

---

## 10. Edge cases & error handling

- **Async meter lag:** graph-backed slots flip only after extraction commits;
  refresh on `extraction_complete` NOTIFY; monotonic within a tribute.
- **Message freeze/skip:** video gated, storybook still generates; agent
  re-invites once next session, never nags.
- **Too few moments:** steering biases hard toward `memories`; storybook
  enforces a minimum page count before its generate is allowed and a **hard
  9-page max** above it (assembly curates the strongest moments to fit).
- **Edits / regenerate:** tribute row follows supersession-style status
  discipline; regenerate re-composes context + re-pushes (reusing the artifact
  regenerate pattern).
- **Boundary recheck:** we only ever write `generation_prompt` +
  `latest_generation_context` and push jobs; Node writes all URL columns + does
  S3. New `artifact_kind`s + the compiled-job schema are **contract additions
  requiring Node-side work** (see §8).

---

## 11. Testing

- Unit tests: checklist probes; percent weighting; status-flow transitions.
- The `tribute_status` view: slot/percent SQL correctness.
- The `message_answer` sidecar path: assert the confession **never reaches
  extraction**.
- Assembly LLM mocked; SQS push mocked.
- Integration tests for `POST /tributes/{id}/generate` gating (video requires
  `ready`; storybook requires min pages).
- Runs against the test DB on `:15432` per the project test setup.

---

## 12. Open items / dependencies

1. **Node compiled-renderer** (other repo) — critical path for the video (§8).
2. Final ingredient weights + the storybook minimum page count (tunable in
   code; pick defaults during implementation). Storybook **max is fixed at 9
   pages**. Video **target length default 45s** (skin-configurable, 30–60s).
3. Confirm the `tribute_status` view can express all probes in SQL, or which
   fall back to agent-stamped fields (§7).
4. Father's Day copy + the expanded archetype question framing (skin content).
