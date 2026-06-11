# Ground-Truth Capture — Design

**Date:** 2026-06-11
**Status:** Approved design, pre-implementation

## Problem

Artifact generation guesses subject context it was never given. Portraits
receive only `name`, `gender` (pronoun), `relationship` — so the model
infers ethnicity/age/era from the name alone and frequently renders the
wrong person (a western man for an Indian subject). Scene artifacts'
base prompts are LLM-emitted at extraction time with no person-level
grounding, so era / clothing / setting drift moment to moment (the same
girl is western in one moment and Indian in another once "Karimnagar"
happens to appear in the segment).

More broadly, the `persons` model is thin (name, relationship, gender,
profile_summary). Stable ground truth about the subject — where their
life happened, roughly when, what they wore, what they looked like —
is never captured, so every consumer (artifact prompts, extraction,
response generator) re-guesses it independently.

Asking the chat LLM to collect this in free conversation is rejected:
it pollutes the transcript, makes the Extraction Worker mine boring
demographic Q&A out of free text, and doesn't scale. Multiple-choice
tap cards (the surface that already exists for coverage taps and
archetype questions) collect it as structured data with one tap.

## Decision summary

| Decision | Choice |
|---|---|
| Scope | Person-level ground truth **and** moment time anchors, both v1 |
| Surface | Existing in-chat tap cards (question + 4 chips + free text + skip). No new UI surface. |
| Placement | **Contextual only:** rides `story`/`deepen` turns when the live story touches an unknown field. Never on `switch` (collides with question-bank selection; user is steering away). Existing intent flow, coverage taps, and steady selection are untouched — this is a strict extension. |
| Face capture | Physical features asked directly (build, glasses, attire — feel like fond remembering). Complexion/ethnicity **never asked** — derived from region + era + cultural context. |
| Storage | `persons.ground_truth` JSONB, fixed field registry in code. No new table. |
| Inference | Extraction Worker emits high-confidence observations as a byproduct; auto-written as `inferred`. Never overwrites explicit answers. |
| Regeneration | **Nothing auto.** Ground truth affects future compositions only; manual regenerate reads it at compose time (that is the recovery path for bad portraits). |
| Onboarding | Two additions (region, birth decade). Everything else contextual-tap or inference. |

## 1. Field registry

Defined in code (new `flashback/ground_truth/registry.py`), like the
anchor dimensions. Each entry: `key`, value shape, `askable` flag,
priority (tie-break only — selection is relevance-first), consumers.

| # | Field | Shape | Asked? | Feeds |
|---|---|---|---|---|
| 1 | `region` | `{country, locale}` | yes (+ onboarding) | portrait, scenes, responder |
| 2 | `birth_era` | decade string `"1950s"` | yes (+ onboarding) | portrait, scenes, anchor-chip derivation |
| 3 | `setting_type` | `village / small town / city / farm` | yes | scenes |
| 4 | `attire` | short free string | yes | portrait, scenes |
| 5 | `distinctive_features` | list (glasses, mustache, braided hair, …) | yes | portrait |
| 6 | `build` | short string | yes | portrait |
| 7 | `cultural_context` | short string ("Telugu Hindu family") | **inferred-only** | portrait, scenes |
| 8 | `era_span` | decades the memories span | **derived-only** (computed from moment time anchors, no LLM) | scenes |
| 9 | `languages` | list | yes (lowest priority) | responder |

Registry rules:

- **Complexion/ethnicity is not a field.** Prompts derive "Indian
  woman, born 1950s, Telangana" from `region` + `birth_era` +
  `cultural_context`.
- **DOB is still never stored** (per CLAUDE.md §1). `birth_era` is a
  decade estimate, captured warmly or inferred, not a date.
- **Boundary with `profile_facts`:** `ground_truth` is the
  machine-consumable layer (prompts/extraction read it);
  `profile_facts` remains the user-facing display Q&A. No shared
  storage. The Profile Summary Generator may read ground truth when
  phrasing facts like `birthplace` / `era`.

## 2. Storage

Migration 0024: `ALTER TABLE persons ADD COLUMN ground_truth JSONB
NOT NULL DEFAULT '{}'`.

Shape — one key per registry field:

```json
{
  "region": {
    "value": {"country": "India", "locale": "Karimnagar, Telangana"},
    "provenance": "tap",
    "confidence": "high",
    "updated_at": "2026-06-11T09:30:00Z"
  }
}
```

- `provenance`: `onboarding | inferred | tap | user_edit`
  (`user_edit` reserved for the v2 Node edit surface).
- **Precedence:** `user_edit > tap > onboarding > inferred`. A write
  with lower precedence than the stored provenance is dropped.
  Inference can refine inference; it never overwrites an explicit
  answer.
- All writes go through one function:
  `upsert_ground_truth_field(person_id, field, value, provenance,
  confidence)` — validates the field against the registry, applies
  precedence, stamps `updated_at`.
- No supersession history in v1; the JSONB is current-state only.

## 3. Capture paths

### 3a. Extraction inference (free)

The `extract_segment` tool schema gains optional
`ground_truth_observations: [{field, value, confidence}]`. The worker
is already reading the whole segment; it additionally notes stable
subject facts ("mentioned Karimnagar → region = India/Telangana,
high"). Persistence routes each observation through the upsert with
`provenance='inferred'`; **only high confidence is written**,
medium/low dropped (invariant #6, under-extract). `cultural_context`
fills exclusively here. `era_span` is recomputed in code from moment
time anchors after extraction commits.

### 3b. Contextual tap

New orchestrator step `select_ground_truth_tap`, after `retrieve`,
before response generation. It only ever *attaches* a tap; it never
alters intent handling, retrieval, or steady question selection.

Code gates, all must pass:

1. intent is `story` or `deepen`
2. `emotional_temperature` is not high
3. GT-tap count this session < **1** (own cap, stricter than
   coverage's 2 — this is intake riding emotional storytelling)
4. ≥ 3 user turns into the session
5. no other tap pending this turn
6. at least one askable registry field is unknown (and not
   `declined` this session)

Then **one small LLM call** (gpt-5.1, `tap_options` pattern) receives
the unknown-fields list, rolling summary, last ~6 turns, subject
name/relationship, and current ground truth. It returns one of:

- `SKIP` — nothing natural to ask, or the answer is already evident
  in this conversation (**the Hyderabad rule, enforced at the last
  gate**). The turn proceeds exactly as today.
- `{kind: "ground_truth", field, question_text, options[4]}` — a
  person-field question the **current story naturally touches**,
  phrased as fond curiosity, never as a form.
- `{kind: "segment_anchor", question_text, options[4]}` — see §4.

When a tap is emitted: the agent's engaged story reply renders
normally and the card rides beneath it (the existing `tap_pending`
acknowledgment-only branch does **not** apply — this tap is a
side-capture, not the next question). `signal_pending_gt_tap` is set
in Working Memory so the Intent Classifier treats a terse chip-style
next message correctly; cleared after one classification.

### 3c. Answer path — structured sidecar, never mined

New optional field on `POST /turn` and `/turn/stream` (precedent:
`question_decision`):

```json
"ground_truth_answer": {
  "kind": "ground_truth",
  "field": "region",
  "option_label": "Karimnagar",
  "free_text": null,
  "skipped": false
}
```

The route persists it **before** the pipeline runs
(`provenance='tap'`), then clears the signal. The conversation never
carries the boring Q&A, so extraction never mines it. `skipped: true`
marks the field `declined` in Working Memory for this session (never
re-asked this session; eligible again later). Free-text answers to
shaped fields (`region`) pass through a small normalizer call;
free-string fields store as-is.

### 3d. Onboarding additions

Two questions appended to every archetype set: where the subject's
life happened (region) and roughly when they were born (decade chips).
Both skippable, warm phrasing. Answers route through the same upsert
with `provenance='onboarding'`. Existing `implies` machinery
untouched.

## 4. Moment anchors

The "about when did this happen?" tap targets the story being told
*right now* — which has no moment row yet (extraction is async). The
answer travels **through the segment payload**, not to a row:

- **Candidacy** rides the same single LLM call (§3b): it picks the
  anchor question when the live story carries no time signal (no
  year, age, or life-period mention) and that is the most natural
  ask. Shared 1-per-session budget — early sessions spend it on
  person fields; once the registry fills, the budget flows to anchors
  naturally.
- **Chips are derived, not guessed.** With `birth_era` known, options
  are computed in code by mapping decades ("When she was a child" /
  "1970s" / "1980s" / "After you came along"). Without it, the LLM
  generates era-neutral chips.
- **Answer path:** same sidecar with `kind: "segment_anchor"`. The
  route stores `{question_text, answer}` in Working Memory on the
  open segment. At the next segment boundary (or wrap force-close)
  it is included in the extraction queue payload as
  `<segment_time_anchor>`. The Extraction Worker prompt treats it as
  authoritative time evidence for the moment(s) of the story it
  references — landing in `time_anchor` / `life_period_estimate`
  where the Coverage Tracker and lifespan derivation already look.
  No new write path to moments.
- Each answered anchor improves the derived `era_span`, which grounds
  scene-artifact decade styling — the two halves feed each other.

**Out of scope (v2):** backfilling anchors on already-extracted
moments (needs moment-targeted taps + a direct `time_anchor` update
path; cleanly separable).

## 5. Consumption

One helper, many readers:
`render_ground_truth_block(ground_truth, audience)` in
`flashback/ground_truth/`. Renders only fields that exist — silent on
unknowns, never "region: unknown".

| Audience | Injection point | Effect |
|---|---|---|
| `extraction` | `<subject_ground_truth>` block in the extraction user message | Scene `generation_prompt`s are **born grounded** ("a 1960s Telangana village kitchen"), the only real scene fix since base prompts are immutable after creation |
| `portrait` | `profile_picture/prompt.py`, beside gender/relationship hints | "Indian woman from Telangana, born in the 1950s, typically in a cotton saree, glasses, slight build" — derived descriptors, read at compose time, so **manual regenerate recovers a bad portrait** |
| `scene` | `artifacts/compose.py` on regenerate/edit | Short era/region/setting modifier line grounds even old generic base prompts on manual regenerate |
| `responder` | compact block on `TurnContext` / `StarterContext` | Agent stops conversationally assuming wrong defaults; languages/cultural context inform tone |

The chip-generation calls (§3b and `tap_options`) also receive the
block, so generated options are region/era-appropriate (saree types,
not blazers).

## 6. API / Node contract changes

- **Migration 0024** — `persons.ground_truth` (above).
- **`POST /turn` (+ `/turn/stream`) request** — optional
  `ground_truth_answer` (§3c), persisted pre-pipeline.
- **`/turn` response metadata** — tap entries gain
  `kind: "coverage" | "ground_truth" | "segment_anchor"` and `field`
  (null for coverage). Existing coverage tap shape otherwise
  unchanged; Node renders all three kinds with the same card.
- **Debug/read surfaces** — person debug surface exposes
  `ground_truth`. Node may read the column directly (read-only, per
  the boundary). A future `POST /ground_truth/upsert` for user edits
  is the v2 hook; `provenance='user_edit'` is reserved for it.
- **Onboarding** — two new questions in every archetype set; answer
  handling routes them to the upsert.
- Docs to update alongside implementation: `API.md`,
  `NODE_INTEGRATION.md`, `SCHEMA.md`, `ARCHITECTURE.md`, `CLAUDE.md`
  (new invariant for the ground-truth layer).

## 7. Error handling

- LLM selection call fails → treated as `SKIP`; turn proceeds
  normally (mirrors `tap_options` best-effort rule).
- Unknown field key in an observation or answer → dropped silently
  (invariant #6 analogue).
- Normalizer failure on free-text → store raw string with
  `confidence='medium'` if the field accepts free strings; otherwise
  drop.
- Sidecar answer for a session with no pending GT tap → ignored
  (idempotent; protects against UI replays).

## 8. Testing

- **Unit:** registry validation; upsert precedence
  (`user_edit > tap > onboarding > inferred`; inference never
  overwrites explicit); gate logic for `select_ground_truth_tap`
  (intent, temperature, cap, turn floor, declined set); anchor-chip
  derivation from `birth_era`; `render_ground_truth_block` per
  audience (silent-on-unknown).
- **Integration:** `/turn` with `ground_truth_answer` persists before
  pipeline; segment payload carries `<segment_time_anchor>`;
  extraction persistence writes `ground_truth_observations` with
  precedence respected; tap metadata `kind`/`field` shape.

## Rejected alternatives

- **LLM-driven gap detection (no registry):** unpredictable, hard to
  cap/test, more LLM calls; the registry + skip-gate achieves the
  same for less.
- **Onboarding-heavy capture:** front-loads survey feel (forbidden by
  CLAUDE.md §1); never-met/ancestor contributors can't answer.
- **`switch`-intent placement:** collides with steady question-bank
  selection and asks intake exactly when the user is steering away.
- **Session-start / wrap / segment-boundary placement:** rejected in
  favor of contextual story/deepen placement — the question is always
  about what is alive in the conversation.
- **New `subject_facts` table / reusing `profile_facts`:** heavier or
  semantically muddled vs. one JSONB column.
- **Auto-regeneration of stale artifacts:** cost explosion risk and
  may clobber artifacts the user liked; manual regenerate reads
  ground truth at compose time instead.
