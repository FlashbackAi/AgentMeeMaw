# Collaborator Feature — Sub-project 2: Speaker-first Retrieval + Attribution

**Date:** 2026-06-15
**Status:** Approved design, pre-implementation
**Git:** This cycle ships with **no commits** (per the user) — all work lands
in the working tree on `feature/collaborator-provenance` for the user to
commit/push to their dev branch.

**Parent strategy:** Collaborator Phase 1 ("Open and Attributed"). This is
sub-project 2 of 6:

1. Provenance foundation — **done** (write-path stamping of `told_by_user_id`).
2. **Per-contributor continuity + speaker-first retrieval + attribution** ← this spec
3. Collaborator onboarding (2-question modal, skip-tolerant nudges)
4. Cross-contributor name recognition (lite)
5. Same-event linking + contradiction review items
6. Cross-contributor identity merges + collaborator removal

## Scope this cycle

Two of sub-project 2's three doc features, both **read-path only**, both
consuming the `told_by_user_id` / `told_by_display_name` columns stamped by
sub-project 1:

- **§2.6 Speaker-first retrieval bias** — when the bot looks up past moments
  during a turn, it prefers the *current speaker's own* contributions, with
  others still in the candidate pool, ranked slightly lower.
- **§2.5 Attribution** — when the bot surfaces a moment authored by a
  *different* contributor, it credits them by name and never presents it as
  the current speaker's own memory.

**Deferred (decision D-CONT below):** §2.7 per-contributor session continuity.
The agent does not persist session summaries today (`GET_SESSION_SUMMARY_SQL`
is a `NULL` placeholder; continuity is Node-supplied via
`session_metadata.prior_session_summary`). Making that per-contributor is a
distinct ownership question (Node vs agent storage) and gets its own later
sub-project.

## Context discovered in the codebase

- `active_moments` is `CREATE VIEW active_moments AS SELECT * FROM moments
  WHERE status = 'active'` (migration 0001). **Correction (found during
  implementation):** Postgres freezes `SELECT *` in a view to the columns that
  existed at view-creation time, so the view did **not** automatically pick up
  the `told_by_*` columns added by 0026. Migration **0027** recreates
  `active_moments` with an explicit column list including the provenance
  columns; it `DROP ... CASCADE`s the dependent `active_themes_with_tier` view
  and recreates it byte-identically from its 0022 definition. So this
  sub-project ships one migration after all.
- Moments are retrieved only on `recall` intent (invariant #19 matrix in
  `orchestrator/steps/retrieve.py`). Both behaviors therefore surface only on
  recall turns — correct and unchanged.
- The current speaker's `user_id` already rides `TurnState.user_id` (added in
  sub-project 1) — it just needs threading into retrieval and the response
  context.
- `MomentResult` (`retrieval/schema.py`) does not yet carry provenance.
- `render_turn_context` (`response_generator/context.py`) renders retrieved
  moments as plain `- <title>: <narrative> (similarity)` lines.
- Entities are retrieved as a shared "cast of characters"; they are
  aggregate, not "things the speaker told us."

## Decisions

### D1 — Speaker-first is a soft additive bias, moments only

In `SEARCH_MOMENTS_SQL`, subtract a small constant `SPEAKER_BIAS` from the
cosine distance of moments whose `told_by_user_id` equals the current
speaker, so own moments rank higher but a sufficiently closer cross-contributor
match can still win. Matches the doc's "slightly lower" wording and the value
of cross-contributor surfacing "when it adds genuine value (corroboration,
filling a gap)"; mirrors the existing `THEME_BIAS_WEIGHT` soft-bias pattern.

- `SPEAKER_BIAS` is a single tunable module constant. Initial value **0.1**
  (cosine distance is 0..2; 0.1 is a gentle nudge). Tunable later toward a
  harder bias if the feel is wrong.
- **Entities get no bias** — they are the shared cast; speaker-first is about
  memories (moments), per §2.6.
- Ordering is applied in SQL (`ORDER BY effective_distance`), not in Python.
  The unbiased `similarity_score` is still returned for display/telemetry.

### D2 — "Own" = exact `told_by_user_id` match; NULL is neutral

`told_by_user_id == current_user_id` → own (gets the bias). `NULL`
(creator-era) and other users → no bias. Consequence: a returning creator's
pre-collaborator `NULL` moments do not receive the own-boost. Accepted — it is
a soft bias, and we do not store a creator user id to disambiguate. Revisit only
if a stored creator id is introduced later.

### D3 — Attribution is name-only, for cross-contributor moments only

A retrieved moment renders with a `told_by="<display_name>"` label **iff**
`told_by_user_id` is present **and** `!= current_user_id`. Own moments and
`NULL` (creator-era) moments render plain (spoken neutrally, no crediting).

- The display name comes from the denormalized `moments.told_by_display_name`
  (no join).
- **Relationship is not available this cycle.** The doc's "name *and
  relationship*" ("Ravi, her brother") needs each contributor's
  relationship-to-subject, which is captured only by collaborator onboarding
  (sub-project 3). Attribution is therefore **name-only** now ("Ravi told us…").
- The response-generator prompt gains an instruction: when drawing on a moment
  marked `told_by` another contributor, credit them by name and never present
  it as the current speaker's own memory.

### D4 — Single-contributor / legacy is a guaranteed no-op

When every retrieved moment is own or `NULL` (the only possibility before
multi-contributor data exists), the bias term is 0 for all rows and no moment
is attributed. Output is byte-for-byte identical to today. Safe to ship into
the current single-contributor product.

### D-CONT — §2.7 continuity deferred

See Scope. Not implemented this cycle.

## Approaches considered (speaker-first mechanism)

- **A. Soft additive bias (chosen).** Distance penalty on others / boost on
  own, ranking stays in SQL. Tunable, keeps cross-contributor surfacing alive.
- **B. Hard tier.** `ORDER BY is_own DESC, distance`. Guarantees all own
  moments outrank all others — contradicts "slightly lower" and blocks
  valuable corroboration. Rejected.
- **C. Fetch-more + re-rank in Python.** More flexible weighting but fetches
  rows we discard and moves ranking out of SQL. Rejected (YAGNI).

## Design

### Retrieval layer

- `retrieval/queries.py` — `SEARCH_MOMENTS_SQL`:
  - add `told_by_user_id`, `told_by_display_name` to both the candidate CTE
    and the outer SELECT;
  - `ORDER BY` becomes the cosine distance minus a `CASE WHEN told_by_user_id
    = %(current_user_id)s THEN %(speaker_bias)s ELSE 0 END` term. The
    `LIMIT` is unchanged.
  - `current_user_id` is bound as a parameter; when it is NULL/empty the CASE
    matches nothing and the bias is a global no-op (D4).
- `retrieval/schema.py` — `MomentResult` gains `told_by_user_id: UUID | None`
  and `told_by_display_name: str | None` (both default `None`).
- `retrieval/service.py` — `search_moments(query, person_id, current_user_id)`
  binds `current_user_id` and the `SPEAKER_BIAS` constant; `SPEAKER_BIAS`
  lives as a module constant in the retrieval package.

### Orchestrator

- `orchestrator/steps/retrieve.py` — the `recall` branch passes
  `current_user_id=state.user_id` into `search_moments`. (`state.user_id` is
  `UUID | None`; an empty/`None` value flows through to the no-op bias.)

### Response generation

- `response_generator/schema.py` — `TurnContext` gains
  `current_user_id: UUID | None = None`.
- `response_generator/context.py` — in `render_turn_context`, the `<moments>`
  block renders each line with a `told_by` label when D3's condition holds;
  otherwise plain. The renderer compares each moment's `told_by_user_id` to
  `ctx.current_user_id`.
- `response_generator/prompts.py` — add the crediting instruction (D3) to the
  turn prompt family.
- The `generate_response` orchestrator step wires `current_user_id` (from
  `state.user_id`) into `TurnContext`, and the retrieved `MomentResult`s
  (now carrying provenance) flow through unchanged.

### Error handling

No new failure modes. All provenance columns are nullable and every code path
treats `NULL`/absent as neutral. A `None`/empty `current_user_id` disables both
the bias (D4) and attribution (nothing is "someone else's" relative to an
unknown speaker — but in practice single-contributor data is all own/NULL, so
nothing is mislabeled).

## Out of scope (later sub-projects)

- §2.7 per-contributor continuity (deferred; ownership decision pending).
- Relationship-aware attribution (needs sub-project 3 onboarding data).
- Attribution on entities/threads (aggregate; no provenance).
- Speaker-first bias on entity retrieval (entities are the shared cast).
- Cross-contributor name recognition in chat (§2.12, sub-project 4).

## Testing

- **Retrieval SQL (DB-gated, `TEST_DATABASE_URL`):**
  - own moment ranks above an other-contributor moment of equal raw distance;
  - a much closer other-contributor moment still ranks above a weakly-relevant
    own moment (proves *soft*, not hard);
  - `MomentResult` rows carry `told_by_user_id` + `told_by_display_name`;
  - `current_user_id=None` → ordering identical to pure-similarity (no-op).
- **Context renderer (no DB):**
  - a moment with `told_by_user_id != current` renders the `told_by` label
    with the display name;
  - own (`== current`) and `NULL` moments render plain;
  - `current_user_id=None` → no labels.
- **Prompt:** the turn prompt contains the crediting instruction.
- **Service wiring (no DB):** `search_moments` forwards `current_user_id`
  and `SPEAKER_BIAS` into the query params.
- **End-to-end no-op:** a single-contributor scenario (all own/NULL) produces
  identical, unattributed output.
- All pre-existing tests must still pass (additive change). Baseline on this
  machine is 14 pre-existing environment failures; the suite must remain at
  exactly that.
