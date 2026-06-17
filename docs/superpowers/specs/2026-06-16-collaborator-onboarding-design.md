# Collaborator Feature — Sub-project 3: Collaborator Onboarding

**Date:** 2026-06-16
**Status:** Approved design, pre-implementation
**Git:** No commits this cycle — all work lands in the working tree on
`feature/collaborator-provenance` for the user to commit/push to their dev
branch.

**Parent strategy:** Collaborator Phase 1 ("Open and Attributed"), sub-project
3 of 6:

1. Provenance foundation — **done** (`told_by_user_id` stamping; migration 0026).
2. Speaker-first retrieval + attribution — **done** (migration 0027; per-contributor
   opener scoping).
3. **Collaborator onboarding** ← this spec.
4. Cross-contributor name recognition (lite).
5. Same-event linking + contradiction review items.
6. Cross-contributor identity merges + collaborator removal.

## Scope this cycle

A collaborator joining an already-established legacy gets a lightweight,
agent-tracked onboarding — distinct from the creator's 5-anchor starter phase:

- **A new `collaborator_onboarding` table** (agent Postgres) holding
  per-`(person_id, user_id)` coverage signals mirrored from Node at session
  start.
- **An `apply_collaborator_onboarding` orchestrator step** (session-start) that
  upserts/mirrors `session_metadata` (`role`, `voice_anchor_text`, modal
  timestamps) into that table and stamps the current contributor's voice anchor
  into Working Memory.
- **Voice-anchor opener:** the collaborator's own first opener is grounded in
  their relationship to the subject ("As his daughter, what's a memory of him
  that's stayed with you?"). Builds on sub-project 2's per-contributor opener
  scoping (fresh contributor → no "last time…", now + relationship framing).
- **Relationship-aware attribution:** completes sub-project 2's deferred piece —
  when one contributor recalls another's moment, the bot credits them by name
  **and** relationship ("Ravi, her brother, told us…"), via a JOIN to the
  contributor's voice anchor.

**Deferred (columns present, behavior later):** the skip-tolerant in-chat nudge
(`select_collaborator_onboarding_tap`, `taps_emitted` cap), the
extraction-worker `first_moment_id` flip, and collaborator removal
(`status='removed'`). The table carries these columns so no future migration is
needed, but no code fills/uses them this cycle.

## Boundary (who owns what)

- **Node / DynamoDB owns authoritative membership:** the roster of which users
  collaborate on which memorial, invite/accept status, `invited_by`, raw modal
  answers, `onboarding_complete`. The agent never reads or mutates this. Per
  CLAUDE.md §3, the agent never touches DynamoDB or Node-owned tables.
- **The agent's `collaborator_onboarding` table is a denormalized mirror /
  read-cache**, not the source of truth. Node passes the relevant fields in
  `session_metadata` on `/session/start`; the agent mirrors them so per-turn
  reads are a local single-row query instead of a cross-service lookup. The
  authoritative copy of modal state stays in DynamoDB and is re-mirrored on each
  session start.
- The agent learns "this is collaborator Keerthi, relationship 'his daughter'"
  purely from `session_metadata` — it never enumerates the membership list.

## Decisions

### D1 — Adopt the prior `collaborator_onboarding` table design (→ migration 0028)
Use the previously-designed table verbatim (renumbered to **0028**, since
`0027` is taken by the sub-project-2 view recreation). One row per
`(person_id, user_id)`, `status active|removed`, partial-unique index on the
active row, `active_collaborator_onboarding` view, `updated_at` trigger. Columns
for the deferred nudge/first-moment/removal are included now (cheap; avoids a
later migration) but left NULL/0/`active`.

### D2 — `apply_collaborator_onboarding` mirrors from `session_metadata`, creator is a no-op
Runs in the session-start pipeline before the opener. Fires only when
`session_metadata.role == 'collaborator'` **and** `state.user_id` is present.
It upserts the active row for `(person_id, user_id)`:
- `voice_anchor_text` / `voice_anchored_at` from `session_metadata.voice_anchor_text`
  (+ `voice_anchored_at`), only when non-empty and not already set (never clobber
  a captured anchor with an empty re-mirror).
- `modal_answered_at` / `modal_dismissed_at` mirrored from metadata when present.
- `status` stays `active`.
Then it stamps the resolved `voice_anchor_text` into Working Memory
(`current_voice_anchor`) so the opener step can read it. A creator session (no
`role`, or no `user_id`) is a complete no-op — no row, no WM signal.

### D3 — Voice anchor grounds the collaborator's OWN opener
`StarterContext` gains `contributor_voice_anchor: str | None`. When present (a
collaborator with a captured anchor), the starter-opener prompt uses it to frame
the opening around their relationship to the subject, without implying prior
conversation (a fresh collaborator still has empty `prior_session_summary` from
sub-project 2, so no "last time…"). When absent, the opener is unchanged.

### D4 — Relationship-aware attribution via JOIN, name-only fallback
`SEARCH_MOMENTS_SQL` gains
`LEFT JOIN collaborator_onboarding co ON co.person_id = m.person_id
AND co.user_id = m.told_by_user_id AND co.status = 'active'`, selecting
`co.voice_anchor_text AS told_by_relationship`. `MomentResult` carries
`told_by_relationship: str | None`. The renderer
(`render_turn_context`) upgrades a cross-contributor moment's label to include
`relationship="..."` when present; the recall prompt credits with name **and**
relationship when available ("Ravi, her brother, told us…"), name-only
otherwise. NULL/creator moments and own moments are unaffected (still no
attribution / plain).

### D5 — Nudge, first-moment flip, removal are out of scope
No `select_collaborator_onboarding_tap` step, no `taps_emitted` increment, no
extraction-worker `first_moment_id` flip, no removal flow this cycle. Columns
exist; behavior is a later sub-project.

## Components

| File | Change | Decision |
|---|---|---|
| `migrations/0028_collaborator_onboarding.up.sql` / `.down.sql` | new table + view + indexes (adopt prior design) | D1 |
| `migrations/0029_expose_told_by_on_active_views.up.sql` / `.down.sql` | recreate `active_entities`/`active_traits`/`active_questions`/`active_profile_facts` with explicit columns incl. `told_by_user_id` (the visibility gap) | — |
| `flashback/collaborator_onboarding/` (new module: `repository.py`, `schema.py`) | upsert + read helpers for the table | D2 |
| `orchestrator/steps/apply_collaborator_onboarding.py` (new) | session-start mirror step | D2 |
| orchestrator session-start pipeline wiring | insert the step before opener | D2 |
| `working_memory/` schema + client | `current_voice_anchor` field | D2/D3 |
| `response_generator/schema.py` | `StarterContext.contributor_voice_anchor`; `MomentResult.told_by_relationship` (via retrieval schema) | D3/D4 |
| `orchestrator/steps/starter_opener.py` | thread `current_voice_anchor` → `StarterContext` | D3 |
| `response_generator/prompts.py` | starter-opener voice-anchor framing; recall-prompt relationship crediting | D3/D4 |
| `response_generator/context.py` | render relationship in cross-contributor attribution | D4 |
| `retrieval/queries.py` + `schema.py` + `service.py` | JOIN + `told_by_relationship` | D4 |

## Data flow

1. Node `/session/start` with `session_metadata.role='collaborator'`,
   `voice_anchor_text`, modal timestamps, and `user_id`.
2. `apply_collaborator_onboarding` upserts the `(person_id, user_id)` row,
   stamps `current_voice_anchor` in WM.
3. Opener step reads `current_voice_anchor` → `StarterContext` → relationship-
   grounded first opener.
4. Later, on a `recall` turn by any contributor, `search_moments` JOINs the
   table → each cross-contributor moment carries `told_by_relationship` →
   renderer + prompt credit "name, relationship".

## Error handling

- Missing/partial `session_metadata` (no `voice_anchor_text`, or
  `role != collaborator`) → step degrades to no-op; no row written; opener and
  attribution behave name-only / unchanged.
- A collaborator with no captured anchor → `told_by_relationship` is NULL →
  name-only attribution; opener is the plain fresh opener.
- The JOIN is a LEFT JOIN — moments with NULL `told_by_user_id` (creator era) or
  no matching onboarding row simply get NULL relationship.

## Testing

- **Migration 0028/0029:** round-trip; `collaborator_onboarding` + view exist;
  the four `active_*` views now expose `told_by_user_id`.
- **Repository (DB-gated):** upsert creates one active row; re-mirror updates
  modal timestamps without clobbering a set `voice_anchor_text`; partial-unique
  index holds.
- **apply step:** collaborator metadata → row + WM `current_voice_anchor`;
  creator/no-`user_id`/no-`role` → no-op; missing `voice_anchor_text` → row with
  NULL anchor, no WM signal.
- **Opener (DB-gated + context):** `StarterContext.contributor_voice_anchor`
  surfaces in the rendered context; prompt has the voice-anchor framing
  instruction.
- **Attribution (DB-gated + render):** a moment told_by a collaborator with a
  voice anchor → `told_by_relationship` populated → renderer emits
  `relationship="..."`; collaborator without anchor → name-only; creator/own →
  plain. Recall prompt has the name+relationship crediting instruction.
- **No regression:** full suite stays at the established baseline (14 no-DB / 28
  with-DB).

## Out of scope (later sub-projects)
- Skip-tolerant in-chat nudge tap + `taps_emitted` cap (§2.3 behavior).
- Extraction-worker `first_moment_id` / `first_moment_recorded_at` flip.
- Collaborator removal (`status='removed'`, sub-project 6).
- Node-side modal rendering / DynamoDB membership (Node owns).
