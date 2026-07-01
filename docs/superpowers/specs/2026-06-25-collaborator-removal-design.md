# Collaborator Feature — Sub-project 6a: Collaborator Removal (reversible hide)

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Parent strategy:** Collaborator feature Phase 1 ("Open and Attributed"). SP6 was
split into two independent specs; this is the first:

- **6a — Collaborator removal (reversible hide)** ← this spec
- 6b — Cross-contributor identity merges (separate, later spec)

## Goal

Let Node remove a contributor from a memorial so their content stops surfacing,
and let them be brought back. Removal is a **reversible hide** — a `status`
flip, never a delete — leaning entirely on the existing `active_*` views, so
there are **no retrieval, response-generator, or UI changes**. A removed
contributor's **moments** disappear; the **entities they introduced that no
surviving contributor references** also disappear; everything else they touched
stays.

## Context discovered in this repo

- `collaborator_onboarding.status` already supports `'active' | 'removed'`
  (migration 0028) with a partial unique index on the active row — so a removed
  row can coexist with a future re-invite, and re-add is idempotent. **Nothing
  flips it today.**
- Every read path filters `status='active'` via the `active_*` views
  (`active_moments`, `active_entities`, `active_edges`, …; migration 0001,
  invariant #1). `active_edges` filters only the **edge's own** status, not its
  endpoints' — but every consumer joins `active_moments`/`active_entities`, so a
  removed endpoint resolves to nothing.
- `moments.status` CHECK is `('active','superseded')`; `entities.status` CHECK is
  `('active','merged')` (migration 0001; later migrations may have widened —
  the migration drops/recreates by current constraint name). Neither yet allows
  `'removed'`.
- Supersession is tracked by the `moments.superseded_by` FK column (not an
  edge). Invariant #5 repoints edges on supersession. The SP5 cross-contributor
  refinement guard (#28) prevents *new* cross-contributor supersessions, but
  pre-guard legacy data contains them (e.g. one contributor's moment superseded
  by another's).
- Foundation rule **D4#1**: only `moments.told_by_user_id` drives hiding/removal;
  entity/trait/question/fact tags are informational. This spec **refines** D4#1:
  entities introduced by the removed contributor *and orphaned to them* are also
  hidden (the GDPR-friendlier "remove what's exclusively theirs").
- Latest migration is 0033. This adds **0034**.

## Decisions

### D1 — Reversible hide via a new `removed` status

Removal flips `status` to `'removed'` on the contributor's `collaborator_onboarding`
row, their `moments`, and their orphaned `entities`. The `active_*` views already
exclude non-active rows, so removed content vanishes from retrieval, the
entity-mention scanner, SP5 feeds, merge-suggestion feeds, themes/threads
counts, and the dev UI — with **zero** changes to any of those code paths.
`'removed'` is **unique to this flow** (supersession uses `'superseded'`, merges
use `'merged'`), which is what makes restore unambiguous.

### D2 — What removal touches (and only this)

`remove_collaborator(person_id, user_id)`, one transaction, in order:

1. `collaborator_onboarding.status → 'removed'` for `(person_id, user_id)`
   (active row only).
2. **Moments:** `told_by_user_id = user AND status='active'` → `'removed'`.
3. **Supersession resurrection (E1):** for moments hidden in step 2, walk the
   `superseded_by` chain **backward** and resurrect the **nearest** predecessor
   owned by a *surviving* contributor → `'active'`. The walk **recurses only
   *through* removed-user-owned moments and stops at the first surviving
   contributor's moment** — so in a chain `M0(X) ← M1(Z) ← M2(Y)` (two different
   survivors), removing Y resurrects only `M1(Z)`, never the deeper `M0(X)`
   (which `M1` still legitimately supersedes). Resurrection leaves the
   resurrected moment's `superseded_by` pointer intact (that pointer is what lets
   restore re-supersede it). This prevents a departing contributor's retelling
   from collateral-hiding a surviving contributor's account. Implementation: a
   `WITH RECURSIVE` walk whose recursion step continues past a node only when
   that node's `told_by_user_id IS NOT DISTINCT FROM user`; the result set is the
   reached moments with a *different* teller and `status='superseded'`.
4. **Orphaned entities:** `told_by_user_id = user AND status='active'` AND — *after
   steps 2–3* — **no `active` moment references the entity** via an
   `involves`/`happened_at` edge → `'removed'`. (Order matters: resurrection runs
   first so a resurrected moment's entities are protected, E2.)
5. Return `{moments_removed, entities_removed, moments_resurrected}`.

Untouched: edges, traits, questions, profile_facts, threads, themes, persons,
coverage_state. (D4#1 — informational/subject-level data stays; edges stay
because consumers gate on active node views, D3.)

### D3 — Edges are never mutated

Removal does not flip any edge. Every read joins `active_moments`/`active_entities`,
so edges to removed nodes resolve to nothing. Leaving edges `active` keeps
restore a pure node-status operation (no edge bookkeeping) and introduces no
leakage, because the only surviving→removed edge possible is an `answered_by`
from a surviving question to a removed moment (orphaned entities have no
surviving referrers by construction). See Testing for the one read-query check
(unanswered-question dedup).

### D4 — Restore is the exact inverse

`restore_collaborator(person_id, user_id)`, one transaction:

1. `collaborator_onboarding.status → 'active'`.
2. Moments `told_by_user_id = user AND status='removed'` → `'active'`.
3. Entities `told_by_user_id = user AND status='removed'` → `'active'`.
4. **Re-supersede (inverse of E1):** for each moment restored in step 2, any
   `active` moment whose `superseded_by` points at it and whose `told_by_user_id`
   is a different contributor → back to `'superseded'`.

Safe because `'removed'` is unique to this flow: step 2/3 only ever touch rows
*this* removal hid. No new columns or side tables needed.

### D5 — Re-invite supports both modes

- **Restore** = `POST /collaborators/restore` (D4) — same `user_id`, content
  returns intact.
- **Fresh start** = no agent endpoint — Node issues a **new `user_id`**; the old
  removed content stays hidden, the returning person is a clean contributor. The
  partial unique index on `collaborator_onboarding (person_id, user_id) WHERE
  status='active'` already permits the removed old row to coexist with the new
  active one.

### D6 — Idempotency

`remove` on an already-removed contributor flips nothing (no active rows match)
and returns zero counts — not an error. `restore` on a non-removed contributor
is likewise a no-op. Both endpoints are safe to call repeatedly.

## Approaches considered

**A. Reversible hide via `status='removed'` (chosen).** Minimal footprint (status
on three tables), no read-path changes, free re-invite, recoverable. The `active_*`
views do all the hiding.

**B. Hard delete / GDPR scrub (rejected for 6a).** `DELETE` of moments, orphaned
entities, their edges, embeddings, **and** every FK referrer (SP5
`moment_same_event_links`/`moment_contradictions`, `identity_merge_suggestions`,
`themed_as`/`answered_by` edges). Irreversible, no re-invite, far larger
cross-table surface. A future follow-up can layer it on, reusing the same
orphan-entity logic.

**C. Supersession-as-edge to ease the E1 chain walk (rejected).** Modelling
`superseded_by` as a `supersedes` edge would let chain-tracing be a generic edge
traversal, but it duplicates the load-bearing `superseded_by` column (two sources
of truth) or forces a full migration off it — rippling through the extraction
path and invariant #5 repointing. The column already supports the recursive walk
(D2 step 3), so edges add cost without benefit here. Unifying moment↔moment
relations into edges is a separate refactor (and SP5 `same_event` can't move to
edges anyway — it needs ack/unlink/reason lifecycle).

## Design

### Migration `0034_moment_entity_removed_status`

- Drop + recreate the `status` CHECK on `moments` to add `'removed'`
  (`'active','superseded','removed'`, plus any value the current constraint
  already carries).
- Same for `entities` (`'active','merged','removed'`, plus current values).
- No view changes (they filter `status='active'`). No new columns.
- `.down.sql` recreates the prior CHECK (after asserting no `'removed'` rows
  remain, or flipping them back to `'active'`).

### Module `flashback.collaborators` (new)

- `repository.py`:
  - `remove_collaborator_async(cursor, *, person_id, user_id) -> RemovalResult`
    — D2 steps 1–5, async cursor, caller owns the transaction.
  - `restore_collaborator_async(cursor, *, person_id, user_id) -> RemovalResult`
    — D4.
  - The supersession-resurrection chain walk is the `WITH RECURSIVE` query in D2
    step 3; the orphan-entity predicate is a `NOT EXISTS` over active
    `involves`/`happened_at` edges to active moments.
- `schema.py`: `RemovalResult` (`person_id, user_id, moments_removed,
  entities_removed, moments_resurrected`).

### HTTP routes (`flashback/http/routes/collaborators.py`, new)

Node-driven, unauthed (§3), mirroring the existing route modules:

- `POST /collaborators/remove` — body `{person_id, user_id}` → `RemovalResult`.
- `POST /collaborators/restore` — body `{person_id, user_id}` → `RemovalResult`.

Registered in `flashback/http/app.py`. `role_id` tolerated/ignored if Node still
sends it (transition window, #26).

### Docs

- CLAUDE.md: new invariant **#29** (reversible collaborator removal — status flip
  on onboarding/moments/orphaned-entities, supersession resurrection, edges
  untouched, restore inverse, both re-invite modes). Note `'removed'` status on
  moments/entities in §5.
- API.md: the two endpoints + `RemovalResult` shape.
- NODE_INTEGRATION.md: removal is agent-owned; Node calls the endpoints, chooses
  restore vs fresh-start; never writes `status` directly.

## Out of scope

- Hard delete / GDPR scrub (approach B) — separate follow-up.
- Cross-contributor identity merges — SP6b.
- Supersession-as-edge refactor (approach C).
- Decrementing `coverage_state` on removal (E5 — denormalized cold-start counter;
  removal must not un-graduate the creator). Left as-is.
- Reversing entity merges that folded a removed contributor's entity into a
  survivor (E7 — merges are permanent/informational).
- Any Node-side UI, and guarding removal during a live session (E9 — Node owns
  session lifecycle).

## Testing

- **Remove (DB):** flips onboarding + the user's active moments + orphaned
  entities to `removed`; a shared entity (referenced by a surviving contributor's
  active moment) stays `active`; an entity introduced by another contributor
  stays; counts correct.
- **Supersession resurrection (DB, E1):** removing the contributor whose moment
  superseded a surviving contributor's moment resurrects that predecessor
  (`superseded → active`, `superseded_by` retained); a length-3 chain resurrects
  the nearest surviving-contributor ancestor; a same-contributor chain resurrects
  nothing (all one departing voice).
- **Ordering (DB, E2):** an entity referenced only by a resurrected moment is
  **not** removed.
- **Restore (DB):** exact inverse — moments/entities/onboarding back to active,
  re-supersede restores the chain; round-trip remove→restore yields the original
  active set.
- **Idempotency (DB, D6):** double remove / restore-when-active are no-ops with
  zero counts.
- **Read isolation:** removed moments absent from retrieval and SP5 `event_links`/
  `contradictions` feeds; removed entities absent from the entity-mention catalog
  and merge-suggestion feeds; all reappear after restore.
- **Unanswered-question dedup (E4):** confirm a question answered only by a
  removed moment re-surfaces (verify the dedup query gates `answered_by` on
  active moments; one-line `JOIN active_moments` fix if not).
- **Endpoints (DB):** remove/restore happy paths + `RemovalResult` shape; unknown
  `(person_id, user_id)` → zero counts (not 404, since removal is idempotent).
- **Non-regression:** additive; existing suites unchanged.
