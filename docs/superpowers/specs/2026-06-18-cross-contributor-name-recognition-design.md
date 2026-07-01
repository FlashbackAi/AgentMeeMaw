# Collaborator Feature — Cross-contributor Name Recognition (lite)

**Date:** 2026-06-18
**Status:** Approved design, pre-implementation
**Git:** No commits this cycle — all work (including this spec) lands in the
working tree on `feature/collaborator-provenance`; the user commits/pushes to
their dev branch.

**Parent strategy:** Collaborator Phase 1 ("Open and Attributed"). This is the
"cross-contributor name recognition (lite)" piece — originally slotted as
sub-project 4, displaced when the question-scoping leak fix took that slot.

## Goal

When a contributor mentions an entity (a person, place, or organization) that
**another contributor introduced**, the agent recognizes it **and credits the
source** — "Priya — Ravi's the one who told us about her" — instead of treating
it as new or unattributed. Recognition is *attributed*, matching the strategy's
"Open and Attributed" principle.

## What already exists (we build on it)

- **Recognition** — the deterministic entity-mention scanner (invariant #20,
  `flashback.entity_mention` + `orchestrator/steps/entity_mention_scan`) matches
  every user turn against a Valkey-cached catalog of the **whole legacy's**
  active entity names+aliases (all contributors, object-kind excluded), loads
  hits via `retrieval.get_entities_by_ids`, and renders them in
  `<mentioned_entities>`. So the agent already *sees* a mentioned entity's
  description regardless of who introduced it.
- **Provenance** — `entities.told_by_user_id` is stamped at first introduction
  and **never restamped on reuse** (invariants #17a, #26). So it reliably means
  "the contributor who first introduced this entity." Entity reuse (a second
  contributor mentioning the same name) folds into the existing row without
  changing `told_by_user_id`.
- **The attribution pattern** — SP2/SP3 already attribute *moments*:
  `MomentResult` carries `told_by_user_id`/`told_by_display_name`/
  `told_by_relationship`; `render_turn_context` emits `told_by="…"
  relationship="…"` on cross-contributor moments; the recall prompt credits
  them. This feature applies the identical pattern to *entities*.

## What's missing (this feature)

`EntityResult` (the scanner's loaded entities) carries **no contributor
provenance** — only `id/kind/name/description/aliases/attributes/created_at`. So
the agent can recognize "Priya" but can't say *who* introduced her. We surface
that provenance and let the agent acknowledge it.

## Decisions

### D1 — Name the contributor via the onboarding row (lite)
Resolve `entities.told_by_user_id` → a display name by `LEFT JOIN
active_collaborator_onboarding` on `(person_id, user_id = told_by_user_id)`,
taking `display_name` and `voice_anchor_text` (relationship). This names
**collaborator-introduced** entities ("the one Ravi mentioned"). Entities
introduced by the **creator / creator-era (`told_by_user_id IS NULL`)**, or any
`told_by_user_id` with no resolvable name, are **still recognized** (listed in
`<mentioned_entities>` as today) but **not name-attributed**. No new
denormalized column, no migration.

### D2 — `EntityResult` gains the same `told_by_*` fields as `MomentResult`
Add `told_by_user_id: UUID | None`, `told_by_display_name: str | None`,
`told_by_relationship: str | None` to `EntityResult` (consistent naming;
resolved via the D1 join rather than a denormalized column).

### D3 — Render cross-contributor attribution in `<mentioned_entities>`
In `render_turn_context`, an entity's line gains `told_by="…"
relationship="…"` attributes when, **exactly mirroring the moment logic**:
`ctx.current_user_id is not None AND entity.told_by_user_id is not None AND
entity.told_by_user_id != ctx.current_user_id AND entity.told_by_display_name`.
The `relationship` attribute is appended only when `told_by_relationship` is
present. Own-contributor entities, NULL-provenance entities, and entities
without a resolved name render exactly as today (name + description, no attrs).
The existing `ambiguous="true"` block attribute is unchanged; per-entity
attribution composes with it.

### D4 — Base-prompt acknowledgment instruction
Add one instruction to `BASE_SYSTEM_PROMPT` (base, because
`<mentioned_entities>` is intent-independent — the scanner runs every turn):
when a `<mentioned_entities>` line carries `told_by`, the agent **may** naturally
acknowledge that another contributor introduced them ("Priya — Ravi's the one
who told us about her") and **must not** force it or restate it mechanically.
This mirrors the existing moment-attribution prompt guidance.

### D5 — Scope: scanner surface only (this cycle)
Provenance is populated and rendered only on the **`get_entities_by_ids`
(entity-mention scanner)** path — the deterministic recognition surface. The
`search_entities` (recall-intent retrieval) path is **out of scope** this cycle;
its results render differently and adding attribution there is a separate,
optional follow-up.

### D6 — Lite boundaries
- **No identity merging** (cross-contributor entity merges are SP6).
- **No retrieval change** (the intent-gated matrix, invariant #19, is untouched).
- **No question-selection change.**
- Entities are **not scope-gated** (only questions are, per SP4 invariant #27);
  the scanner already surfaces the whole legacy's entities, so this is purely
  "open *and now attributed*" — consistent with the strategy, no new gate.

## Components

| File | Change | Decision |
|---|---|---|
| `src/flashback/retrieval/queries.py` | `get_entities_by_ids` SQL: select `told_by_user_id`; `LEFT JOIN active_collaborator_onboarding co ON co.person_id = e.person_id AND co.user_id = e.told_by_user_id` (alias as needed); select `co.display_name`, `co.voice_anchor_text` | D1 |
| `src/flashback/retrieval/schema.py` | `EntityResult` gains `told_by_user_id`, `told_by_display_name`, `told_by_relationship` (default None) | D2 |
| `src/flashback/retrieval/service.py` | map the new columns into `EntityResult` in `get_entities_by_ids` | D1/D2 |
| `src/flashback/response_generator/context.py` | `<mentioned_entities>` per-entity line: cross-contributor `told_by`/`relationship` attrs | D3 |
| `src/flashback/response_generator/prompts.py` | base-prompt acknowledgment instruction | D4 |
| `CLAUDE.md` | brief note under invariant #20 (entity-mention scanner now surfaces cross-contributor provenance) | docs |

## Data flow

1. Contributor `Y` types a message mentioning "Priya". The scanner matches it
   against the legacy catalog → `get_entities_by_ids` loads the Priya entity,
   now carrying `told_by_user_id = <Ravi>` and (via the join) `told_by_display_name
   = "Ravi"`, `told_by_relationship = "his son"`.
2. `render_turn_context` renders
   `- person Priya: <description> told_by="Ravi" relationship="his son"` because
   `told_by_user_id` (Ravi) ≠ `current_user_id` (Y) and a name resolved.
3. The base prompt lets the response generator acknowledge: "Priya — Ravi's the
   one who first told us about her." When Y *is* the introducer, or Priya was
   creator-introduced (NULL / no name), no attribution renders.

## Error handling

- `told_by_user_id IS NULL` (creator era) → no join match → no attribution;
  entity still listed (recognized).
- `told_by_user_id` set but no `active_collaborator_onboarding` row (creator
  stamped id, or a removed collaborator) → join yields NULL name → no
  attribution; entity still listed.
- `current_user_id IS NULL` (creator session) → the `!=` guard means a
  collaborator-introduced entity *is* attributed to that collaborator (correct:
  the creator hears "Ravi mentioned her"); a NULL-provenance entity is not.
- Ambiguous mention (one surface form → ≥2 entities) → `ambiguous="true"` block
  as today; each entity carries its own attribution independently.

## Testing

- **Query/join (DB-gated):** an entity introduced by a collaborator returns
  `told_by_display_name`/`told_by_relationship` from the onboarding row; an
  entity with NULL `told_by_user_id` returns NULLs; an entity whose
  `told_by_user_id` has no onboarding row returns NULLs.
- **`EntityResult` schema:** new fields default to None.
- **Render (`render_turn_context`):** cross-contributor entity → `told_by` +
  `relationship` attrs; own-contributor entity → none; NULL/creator entity →
  none; entity with name but no relationship → `told_by` only; `ambiguous`
  composes.
- **Prompt:** `BASE_SYSTEM_PROMPT` contains the cross-contributor entity
  acknowledgment instruction; it's present in every assembled intent prompt
  (reuse the existing base-prompt-in-every-intent test pattern).
- **No regression:** no-DB + DB suites stay at baseline.

## Out of scope (later)
- `search_entities` (recall-path) attribution — optional follow-up.
- Universal naming for creator-introduced entities (would need a denormalized
  name on entities or a moment-based resolution).
- Identity merges / dedup across contributors (SP6).
