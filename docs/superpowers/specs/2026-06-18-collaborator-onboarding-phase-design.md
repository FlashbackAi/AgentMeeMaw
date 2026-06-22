# Collaborator Feature — Collaborator Onboarding Phase (SP3 deferred D5)

**Date:** 2026-06-18
**Status:** Approved design, pre-implementation
**Git:** No commits this cycle — all work (including this spec) lands in the
working tree on `feature/collaborator-provenance`; the user commits/pushes to
their dev branch.

**Parent strategy:** Collaborator Phase 1 ("Open and Attributed"). This
completes the piece deferred in sub-project 3 (decision D5): the in-chat
onboarding nudge, the `taps_emitted` cap, and the `first_moment_id`
graduation.

## Goal

A collaborator who joins an already-established legacy is gently, **indirectly**
drawn into contributing their first memory — capturing *what the subject meant
to them* through a story, never a direct "what did they mean to you?" survey.
This is modelled as a lightweight **2-item onboarding phase** that mirrors the
creator's 5-anchor starter phase 1:1, but with its own sticky flag on
`collaborator_onboarding` (never `persons.phase`).

## The mirror

| | Creator starter phase | Collaborator onboarding (this) |
|---|---|---|
| Checklist | `persons.coverage_state` — 5 anchors | **2 items** (connection, memory) — derived |
| Surface | coverage tap cards | onboarding tap card (same surface) |
| Tracker (code) | Coverage Tracker increments anchors | first-moment flip + derived satisfaction |
| Flag flip (sticky) | Handover Check → `persons.phase='steady'` | **Onboarding Check → `collaborator_onboarding.phase='active'`** |

## The 2-item questionnaire

Both items are captured **indirectly**; each is "satisfied" by a signal,
mirroring how a creator anchor is satisfied by extracted content. Both signals
are **derived from columns that already exist** on `collaborator_onboarding`
(migration 0028) — no new checklist storage:

1. **Connection** — how the contributor knew the subject / their bond.
   *Satisfied* when `voice_anchor_text IS NOT NULL OR modal_answered_at IS NOT
   NULL OR modal_dismissed_at IS NOT NULL`. `voice_anchor_text` is filled from
   **two sources** (non-clobber, form-first): the Node onboarding modal
   (mirrored at session start, SP3), **or** the **agent inferring the
   relationship from the contributor's own words** during extraction (see D8).
   The agent never *asks* the connection question directly — it derives it from
   what they naturally say.
2. **Defining memory** — one standout moment with the subject.
   *Satisfied* when the collaborator's **first moment** (`told_by_user_id =
   them`) is extracted, flipping `first_moment_id`. This is the item the
   **agent actively nudges**.

The phase flips to `active` when **both** are satisfied. Significance ("what
the subject meant to them") is mined from the elicited story by the existing
Extraction Worker (moment + `emotional_tone` + traits) — there is no new
significance field (anti-survey; YAGNI).

## Decisions

### D1 — Sticky `phase` flag on `collaborator_onboarding` (migration 0032)
Add the direct analog of `persons.phase`:
- `phase TEXT NOT NULL DEFAULT 'onboarding' CHECK (phase IN ('onboarding','active'))`
- `phase_locked_at TIMESTAMPTZ`

Because `active_collaborator_onboarding` is a `SELECT *` view, the migration
**recreates the view** (Postgres freezes `SELECT *` columns at creation — same
gotcha handled in migrations 0027 / 0029 / 0030). The down migration drops the
view, drops the two columns, and recreates the view. Distinct from Node's
membership `onboarding_complete` (DynamoDB-owned): `phase` is agent-internal and
drives nudging only.

### D2 — Satisfaction is derived, not stored
`connection_satisfied` and `memory_satisfied` are computed from existing
columns (see the questionnaire section). No `onboarding_state` JSONB is added —
the two existing column groups (`voice_anchor_text`/`modal_*`,
`first_moment_id`) already carry the signals, mirroring how `coverage_state`
counters are read but here derived directly.

### D3 — `first_moment_id` flip (Extraction Worker)
Inside the existing extraction transaction, when the **first** moment with
`told_by_user_id = <collaborator>` for this `person_id` commits **and** the
active `collaborator_onboarding` row's `first_moment_id IS NULL`, set
`first_moment_id` + `first_moment_recorded_at`. Idempotent — a second moment
never overwrites. A NULL `told_by_user_id` (creator era) never triggers this.

### D4 — Onboarding Check (code; mirrors Handover Check)
A small code routine flips `phase 'onboarding' → 'active'` and stamps
`phase_locked_at` when `connection_satisfied AND memory_satisfied AND
phase = 'onboarding'`. **Sticky** — no auto-revert. Runs at two points:
- **Tail of the extraction transaction**, right after the `first_moment_id`
  flip (the common graduation trigger).
- **Session start**, inside `apply_collaborator_onboarding`, after the modal
  state is mirrored (catches a returning collaborator whose memory was recorded
  in a prior session and whose modal just resolved).

### D5 — Onboarding nudge step `select_collaborator_onboarding_tap`
A new orchestrator step in the **`/turn` pipeline**. For an **active**
collaborator (`state.user_id` present, an active `collaborator_onboarding` row
exists) with `phase = 'onboarding'` and `memory_satisfied = false`, it emits the
existing **tap-card surface** (prompt + 4 LLM option chips + free-text + skip)
carrying the **indirect "defining memory" prompt**.

- **Cadence:** once per session (gated by a Working Memory flag
  `collaborator_onboarding_tap_emitted`), on the collaborator's **first turn**
  of the session — reusing the `/turn` tap surface and the existing
  `tap_pending` response-generator branch (acknowledgment-only reply; the card
  is the question). `/session/start` is unchanged (it still never carries taps,
  per §9).
- **No ceiling:** the nudge fires once per session **every session until the
  collaborator records their first memory** (phase → `active`). The only exit is
  real graduation — there is no stop-after-N cutoff (kept deliberately simple).
  `collaborator_onboarding.taps_emitted` still increments each emit, but purely
  as a diagnostic counter — it gates nothing.
- **Precedence:** runs **before** `select_coverage_tap`; if it sets
  `state.taps`, the coverage-tap step early-returns. (On a `steady` legacy
  coverage taps don't fire anyway; this only matters for the rare
  `starter`-legacy-with-collaborator case — onboarding wins.)

### D6 — Indirect prompt + chips
`tap_options` gains an onboarding variant: given subject name + the
contributor's voice anchor (relationship), it returns an **indirect, warm**
defining-memory prompt and 4 concrete option chips (e.g. for "his daughter":
*"When you picture your dad, what's a small, ordinary moment with him that still
comes back to you?"*). Best-effort: on LLM failure the card falls back to the
prompt + free-text only (mirrors coverage-tap behavior).

### D8 — Agent-derived connection (relationship inferred from conversation)
The Extraction Worker, when processing a **collaborator** segment (a session
with a non-NULL `told_by_user_id`), derives an optional short
`contributor_relationship` phrase — the contributor's relationship to the
subject, inferred from their own words ("my dad…" → "his daughter") — as a new
optional field on the existing `extract_segment` LLM tool output. Persistence
writes it to `collaborator_onboarding.voice_anchor_text` +
`voice_anchored_at` **only if `voice_anchor_text IS NULL`** (non-clobber — a
form-supplied anchor always wins; mirrors the `UPSERT_ONBOARDING_SQL` COALESCE
rule). This is the agent-side path that satisfies the Connection item without
the form, and it runs in the same extraction transaction as the
`first_moment_id` flip (D3) and the Onboarding Check (D4) — so a single
memory-rich answer can satisfy **both** items and graduate the collaborator at
once. Best-effort: when the LLM omits the field, nothing is written and the
form path still applies. The trait-description contributor-exclusion rule
(invariant #18c) is unaffected; this writes only the contributor's own
relationship phrase to their onboarding row, never into subject content.

### D7 — Out of scope (later)
- The agent **actively asking the connection question** in-chat — the agent
  *derives* connection from the contributor's words (D8) and *mirrors* the form,
  but it never poses a direct "what's your relationship?" question.
  **Showing the connection popup is a Node/frontend responsibility**, driven by Node's own
  DynamoDB membership record (new collaborator without a captured connection);
  the agent neither owns nor signals that trigger. The agent only *receives* the
  modal's result in `session_metadata` at session start. If Node wants a read
  signal it may read `active_collaborator_onboarding.voice_anchor_text`
  (NULL = not captured) from Postgres, but the trigger lives in Node. No
  agent-side work for the popup.
- Collaborator **removal** (`status='removed'`) — sub-project 6.
- Capturing structured fields (e.g. `voice_anchor_text`) from a tap answer —
  tap answers are plain user turns mined by normal extraction.

## Components

| File | Change | Decision |
|---|---|---|
| `migrations/0032_collaborator_onboarding_phase.{up,down}.sql` (new) | `phase` + `phase_locked_at` columns; recreate `active_collaborator_onboarding` view | D1 |
| `src/flashback/collaborator_onboarding/queries.py` | SQL: read phase + satisfaction columns; UPDATE first_moment; flip phase (guarded `WHERE phase='onboarding'`) | D3/D4 |
| `src/flashback/collaborator_onboarding/repository.py` | `get_onboarding_state`, `mark_first_moment`, `flip_phase_if_complete`, `increment_taps_emitted` helpers | D3/D4/D5 |
| `src/flashback/orchestrator/steps/apply_collaborator_onboarding.py` | after mirroring modal state, run the Onboarding Check | D4 |
| `src/flashback/orchestrator/steps/select_collaborator_onboarding_tap.py` (new) | the nudge step | D5 |
| `src/flashback/orchestrator/steps/select_coverage_tap.py` | early-return if `state.taps` already set | D5 |
| orchestrator `/turn` pipeline wiring (`orchestrator.py`, JSON + stream) | insert `select_collaborator_onboarding_tap` before `select_coverage_tap` | D5 |
| `src/flashback/orchestrator/tap_options.py` | onboarding prompt + chips variant | D6 |
| `src/flashback/working_memory/` (schema + client) | `collaborator_onboarding_tap_emitted` per-session flag | D5 |
| `src/flashback/workers/extraction/{schema,prompts}.py` | optional `contributor_relationship` field on `extract_segment` + rubric (collaborator sessions only) | D8 |
| `src/flashback/workers/extraction/persistence.py` (or `worker.py`) | first-moment flip + non-clobber `voice_anchor_text` write (D8) + Onboarding Check, all at tx tail | D3/D4/D8 |
| `CLAUDE.md` | document the collaborator onboarding phase (§6-adjacent + a note on invariant #26 first_moment usage) | docs |

## Data flow

1. **Session start (collaborator):** `apply_collaborator_onboarding` mirrors the
   modal state (voice anchor / modal_*). Connection may now be satisfied. It runs
   the Onboarding Check (flips to `active` if a prior session already recorded a
   memory). Opener is the SP3 voice-anchor opener.
2. **First turn:** `select_collaborator_onboarding_tap` sees `phase='onboarding'`
   + `memory_satisfied=false` + not-yet-emitted-this-session + under ceiling →
   emits the indirect defining-memory tap. Response generator runs the
   `tap_pending` branch (acknowledge-only). `taps_emitted++`; WM flag set.
3. **Collaborator replies** (chip or free text) → normal turn → segment →
   Extraction Worker. In the same tx: the first moment with `told_by = them`
   commits → `first_moment_id` flips (memory ✓); the inferred
   `contributor_relationship` writes `voice_anchor_text` if NULL (connection ✓,
   D8); the Onboarding Check then flips `phase='active'`. One good answer can
   satisfy both items at once.
4. **Subsequent sessions:** `phase='active'` → the nudge step is a no-op.

## Error handling

- Not a collaborator / no `state.user_id` / no active onboarding row → nudge
  step and check are no-ops.
- `phase` already `active` → nudge step no-op (sticky).
- `tap_options` LLM failure → prompt + free-text card (no chips).
- Onboarding Check is guarded by `WHERE phase='onboarding'` so concurrent
  session-start + extraction-tail runs can't double-stamp `phase_locked_at`.
- A superseded/merged first moment does not un-flip `first_moment_id` (sticky;
  the flip only ever sets, never clears).

## Testing

- **Migration 0032:** round-trip; `phase`/`phase_locked_at` exist; the view
  exposes them; down migration succeeds despite the view dependency (drop-view
  / drop-columns / recreate-view, as in 0030).
- **Satisfaction derivation:** connection satisfied by any of
  voice_anchor/modal_answered/modal_dismissed; memory satisfied by
  first_moment_id; unit tests over the boolean logic.
- **first_moment flip (DB):** first collaborator moment sets
  first_moment_id+timestamp; second moment doesn't overwrite; creator-era NULL
  moment never flips.
- **Agent-derived connection (D8):** a collaborator segment yielding a
  `contributor_relationship` writes `voice_anchor_text` when NULL; does NOT
  overwrite a form-supplied anchor; a memory-rich first segment satisfies both
  items and graduates in one extraction tx.
- **Onboarding Check (DB):** flips only when both satisfied; sticky (re-run
  no-ops); guarded against double-stamp.
- **Nudge step:** fires once/session, every session, while onboarding +
  memory-unsatisfied; no-op when active or not a collaborator; sets `state.taps`
  with the memory prompt; increments taps_emitted (diagnostic only); precedence
  over coverage tap.
- **tap_options onboarding variant:** returns prompt + 4 chips; falls back on
  failure.
- **No regression:** no-DB + DB suites stay at baseline.
