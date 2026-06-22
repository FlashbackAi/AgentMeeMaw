# Collaborator Feature — Sub-project 4: Contributor-scoped question selection

**Date:** 2026-06-17
**Status:** Approved design, pre-implementation
**Git:** No commits this cycle — all work (including this spec) lands in the
working tree on `feature/collaborator-provenance` for the user to commit/push
to their dev branch.

**Parent strategy:** Collaborator Phase 1 ("Open and Attributed"), sub-project
4 of 6:

1. Provenance foundation — **done** (`told_by_user_id` stamping; migration 0026).
2. Speaker-first retrieval + attribution — **done** (migration 0027).
3. Collaborator onboarding — **done** (migrations 0028/0029).
4. **Contributor-scoped question selection** ← this spec.
5. Same-event linking + contradiction review items.
6. Cross-contributor identity merges + collaborator removal.

> SP4 was originally scoped as "cross-contributor name recognition (lite)."
> Manual testing reframed it: the pressing, observed gap is that question
> selection leaks one contributor's content into another contributor's
> session. Name recognition is deferred; this sub-project closes the leak.

## Problem (observed in manual testing)

Legacy subject = a father. Creator = the son (dev `user_id` was `null`).
Collaborators = the daughter and a friend.

- **Leak #1:** The daughter (collaborator) told a moment about being with her
  father and brother (the son). In the son's *next* session the agent asked him
  to continue *the daughter's* moment — as if he had told it.
- **Leak #2:** A second collaborator (the father's friend) was asked "how he
  used to be with his daughter" — content that exists only because the daughter
  collaborator entered herself.

### Root cause

Question selection is **person-scoped, not contributor-scoped**, and the
producer bank is mined from the whole cross-contributor graph:

- `SELECT_STEADY_CANDIDATES` (`phase_gate/queries.py`) filters only by
  `q.person_id`. No `told_by_user_id` awareness.
- `SteadySelector.select()` (`phase_gate/steady_selector.py`) takes
  `person_id` + `session_id` but never a `user_id`.
- Producers (P2/P3/P5, `thread_deepen` P4, `dropped_reference` P1) write
  questions derived from any contributor's moments/entities/threads into one
  shared per-legacy bank.

So every question is eligible for every contributor's session, presented as if
the current contributor owns it.

The opener continuity path is *not* affected — `_build_continuity_summary`
(`orchestrator/steps/starter_opener.py`) is already scoped by
`told_by_user_id` (SP2). Retrieval's speaker bias is soft-by-design (open +
attributed). The leak is specifically the **question** path.

## Scope this cycle

A **combined** model on two orthogonal axes:

- **Provenance** (deterministic, code) — *who* a question came from, via
  `told_by_user_id`.
- **Scope** (LLM-emitted label) — *how sensitive* a question is:
  `public | personal | private`.

The LLM picks the **label**; **code** enforces eligibility deterministically.
This honors CLAUDE.md §10 (code over LLM for orchestration): the privacy gate
is SQL over `told_by_user_id`, not a trust-the-LLM decision.

**Deferred:** relationship-based "personal" (close family of the subject can
see a `personal` question even if another contributor authored it). This cycle
`personal` is purely provenance-based — no relationship-closeness lookup. Tier
semantics are designed so that upgrade is additive later.

## Decisions

### D1 — Three scope tiers; LLM labels, code enforces

The producer LLM emits `attributes.scope ∈ {public, personal, private}` per
question, judged on content sensitivity. Eligibility for the current
contributor (`uid` = `state.user_id`, which is NULL for the creator era) is
enforced in SQL:

| Scope | LLM picks it when… | Who is eligible (code) |
|---|---|---|
| `public` | general / shareable (work, hobbies, shared events, personality) | **Everyone** — provenance ignored |
| `personal` | relationship-textured but not sensitive (parenting, home life, rituals) | **Own + shared** — `told_by_user_id IS NULL OR told_by_user_id = uid` |
| `private` | intimate / sensitive (health, struggles, conflict, money, secrets) | **Teller only** — `told_by_user_id IS NOT DISTINCT FROM uid` (NULL-safe) |

Rationale for the tier semantics without a relationship graph:
- `personal` == the provenance backbone: a contributor's own questions plus the
  shared/creator-era (NULL) foundation, but **never another collaborator's**.
- `private` == strictly the teller: even a creator's `private` question
  (`told_by_user_id IS NULL`) is visible only to the creator (whose `uid` is
  also NULL — matched NULL-safe), and a collaborator's `private` question is
  invisible to everyone else including the creator.

### D2 — Combined eligibility filter

Added to `SELECT_STEADY_CANDIDATES` (and the coverage-tap selectors):

```sql
AND (
     COALESCE(q.attributes->>'scope', 'personal') = 'public'
  OR (COALESCE(q.attributes->>'scope', 'personal') = 'personal'
        AND (q.told_by_user_id IS NULL OR q.told_by_user_id = %(current_user_id)s))
  OR (COALESCE(q.attributes->>'scope', 'personal') NOT IN ('public', 'personal')
        AND q.told_by_user_id IS NOT DISTINCT FROM %(current_user_id)s)
)
```

Every branch wraps `scope` in `COALESCE(..., 'personal')` so a row with **no**
`scope` attribute (every pre-SP4 row; any the LLM omits) is evaluated as
`personal` — the safe provenance rule (D3). `current_user_id` is a single
param; SQL `NULL` semantics make it correct for both the creator (`uid` NULL →
`= NULL` never true; `IS NOT DISTINCT FROM NULL` matches NULL rows) and a
collaborator. The third branch catches `private` **and** any unexpected/garbage
label, treating non-`public`/non-`personal` as teller-only (fail-safe: unknown
sensitivity → most restrictive).

### D3 — Default for untagged / legacy questions = `personal`

`COALESCE(scope, 'personal')` for the personal/private branches. Questions with
no `scope` attribute (every pre-SP4 row; any the LLM omits) behave as
`personal` — the safe provenance rule. Consequence: existing creator-era NULL
questions remain visible to all contributors (shared foundation), but no
collaborator's content leaks. `public` is an explicit LLM *widening*; `private`
an explicit LLM *narrowing*.

### D4 — Producer LLMs emit `scope`; persistence writes it into `attributes`

Each question-generating LLM gains a required `scope` field in its tool schema
plus a short rubric in its prompt:

- Producers P2/P3/P5 — `workers/producers/schema.py` + `prompts.py`.
- `thread_deepen` P4 — `workers/thread_detector/p4_llm.py` + `prompts.py`.
- `dropped_reference` P1 — inline in `workers/extraction` (schema + prompt).

Persistence already writes the `attributes` dict; it simply includes `scope`.
**No migration** — `scope` lives in the existing `attributes` JSONB. Persistence
defensively defaults a missing/invalid `scope` to `personal` on write so the row
is self-describing.

Migration-seeded `coverage_tap` rows (P0) are generic cold-start prompts → set
`scope='public'` in the seed (a data update migration, see D7), so they stay
open to all.

### D5 — `thread_deepen` derives `told_by_user_id` from its thread

`personal`/`private` gating depends on `told_by_user_id`, but `thread_deepen`
questions are inserted with NULL today (`thread_detector/persistence.py`).
At production time, derive it from the motivating thread's member moments
(reverse `evidences` edges → `active_moments.told_by_user_id`):

- All members collapse to a single distinct contributor (treating NULL as
  unowned/creator) → stamp that contributor's `user_id`.
- Members span two or more distinct collaborators → leave NULL (genuinely
  shared cross-contributor arc; a `personal`/`public` question stays open, a
  `private` one is teller-only which for NULL means creator-only).

This is the only **new** provenance-derivation logic. P1 `dropped_reference`
already stamps the extraction session's `told_by`; per-session P2/P3/P5 already
stamp the session `user_id` (verify during implementation).

### D6 — Wiring `current_user_id` through selection

Thread `state.user_id` from the orchestrator into:

- `SteadySelector.select(..., current_user_id)` →
  `_fetch_candidates` → `SELECT_STEADY_CANDIDATES`.
- `phase_gate.select_next_question(..., current_user_id)` (used by the
  starter-phase opener `select_starter_question` and the steady `/turn`
  selection step).
- `SELECT_UNANSWERED_COVERAGE_TAP` / `SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION`
  (coverage taps are `told_by` NULL + `public`, so they pass, but the filter is
  added for consistency and to cover the starter-phase **promote-seeded-to-tap**
  path, which runs a producer-bank question through the steady selector).

Where the orchestrator has no `user_id` (creator era), `current_user_id` is
NULL and the filter degrades to "NULL-`told_by` only for personal, teller-only
for private, public for all" — correct.

### D7 — Collaborator display name (rider)

Add `display_name TEXT` to `collaborator_onboarding` (migration 0030) and
mirror it from `session_metadata.contributor_display_name` in
`apply_collaborator_onboarding`. A denormalized convenience mirror (Node /
DynamoDB stays authoritative) so the dev UI / provenance panel can label
entity/trait/fact rows by name without an in-memory roster. Never clobber a set
value with an empty re-mirror (COALESCE, mirroring the voice-anchor upsert).

### D8 — Seed migration for `coverage_tap` scope

Migration 0031: `UPDATE questions SET attributes = jsonb_set(attributes,
'{scope}', '"public"') WHERE source = 'coverage_tap' AND person_id IS NULL`.
Idempotent; down-migration removes the key.

## Components

| File | Change | Decision |
|---|---|---|
| `phase_gate/queries.py` | combined eligibility filter + `current_user_id` param in `SELECT_STEADY_CANDIDATES`, `SELECT_UNANSWERED_COVERAGE_TAP`, `SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION` | D2/D6 |
| `phase_gate/steady_selector.py` | `select(..., current_user_id)` → `_fetch_candidates` param | D6 |
| `phase_gate/` next-question entry (`select_next_question`) | thread `current_user_id` | D6 |
| `orchestrator/steps/starter_opener.py` (`select_starter_question`) | pass `state.user_id` | D6 |
| orchestrator steady `/turn` selection step | pass `state.user_id` | D6 |
| `workers/producers/schema.py` + `prompts.py` | `scope` field + rubric | D4 |
| `workers/producers/persistence.py` | write `scope` (default `personal`) into attributes | D4 |
| `workers/thread_detector/p4_llm.py` + `prompts.py` | `scope` field + rubric | D4 |
| `workers/thread_detector/persistence.py` | derive + stamp `told_by_user_id`; write `scope` | D5/D4 |
| `workers/extraction/*` (P1 dropped_reference) | `scope` field + rubric; write into attributes | D4 |
| `migrations/0030_collaborator_onboarding_display_name.{up,down}.sql` | `display_name` column | D7 |
| `orchestrator/steps/apply_collaborator_onboarding.py` + `collaborator_onboarding/{queries,repository}.py` | mirror `display_name` | D7 |
| `migrations/0031_coverage_tap_scope_public.{up,down}.sql` | seed `coverage_tap` scope | D8 |
| `CLAUDE.md` | update invariant #26 (thread_deepen provenance); add invariant for scope tiers | docs |

## Data flow

1. A producer (P1/P2/P3/P5/P4) generates a question. The LLM emits
   `scope ∈ {public, personal, private}` from the content.
2. Persistence writes the question with `attributes.scope` and a
   `told_by_user_id` (session `uid` for per-session producers / extraction;
   derived from thread membership for `thread_deepen`; NULL for genuinely shared
   / cadence / creator-era).
3. On a later session for contributor `Y` (`uid = Y`, or NULL for creator), the
   steady/starter selector runs `SELECT_STEADY_CANDIDATES` with
   `current_user_id = uid`. The combined filter keeps only:
   `public` (all), `personal` (own + NULL/shared), `private` (teller only).
4. Result: `Y` is never asked another collaborator's `personal`/`private`
   question; `public` family lore still flows across contributors.

## Error handling

- **Missing `scope`** on a row → treated as `personal` via `COALESCE` (D3) and
  written as `personal` on new inserts (D4). No crash, safe default.
- **Invalid `scope` string** → falls into the third (`private`) branch
  (fail-safe: unknown sensitivity → most restrictive).
- **`current_user_id` NULL** (creator era / no `user_id` on request) → filter
  degrades correctly via NULL semantics (D2/D6).
- **`thread_deepen` derivation** fails or thread has no resolvable members →
  leave `told_by_user_id` NULL (current behavior; question is shared).
- **Producer LLM omits `scope`** → persistence default (`personal`).

## Testing

- **Eligibility matrix (DB-gated):** each scope tier × `{own, other-collaborator,
  NULL/creator-era}` × `{collaborator uid, creator NULL uid}` →
  `SELECT_STEADY_CANDIDATES` returns/excludes correctly. Specifically:
  - `public` → returned for all.
  - `personal` own → returned; `personal` other-collaborator → excluded;
    `personal` NULL → returned for all.
  - `private` own → returned; `private` other-collaborator → excluded;
    `private` NULL → returned only when `uid` is NULL (creator).
  - untagged (no `scope`) behaves as `personal`.
- **`thread_deepen` derivation (DB-gated):** single-contributor thread → question
  stamped that contributor; mixed-contributor thread → NULL.
- **Producer/extraction schema + prompt:** `scope` is a required field; the
  rubric instruction is present; persistence writes it; missing → `personal`.
- **Selector wiring:** `current_user_id` threads from orchestrator state to SQL.
- **Reproduce both reported leaks** as failing tests, then green:
  daughter's `underdeveloped_entity` excluded from the son's and the friend's
  banks; daughter's `dropped_reference` excluded from the son's bank.
- **`display_name` (DB-gated):** mirrored on collaborator session start; not
  clobbered by an empty re-mirror.
- **No regression:** no-DB suite at 14 baseline; DB-gated suite at 28 baseline.

## Out of scope (later sub-projects)

- Relationship-based `personal` (close family of the subject sees a `personal`
  question authored by another contributor) — needs a relationship-closeness
  lookup over `collaborator_onboarding` voice anchors.
- Attribution of cross-contributor `public` questions in the opener / response
  ("Your sister mentioned…") — SP4 scopes; richer attribution is SP2/SP5 polish.
- Cross-contributor name recognition (the original SP4 framing).
- Same-event linking + contradiction review (SP5); identity merges + removal
  (SP6).
