# Collaborator Feature — Sub-project 6b: Cross-Contributor Identity Merges

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Parent strategy:** Collaborator feature Phase 1 ("Open and Attributed"). Second
half of SP6 (after 6a collaborator removal):

- 6a — Collaborator removal (reversible hide) — **done** (migration 0034).
- **6b — Cross-contributor identity merges** ← this spec.

## Goal

Make entity merges **provenance-correct** and **visible across contributors**.
The existing `identity_merges` machinery already detects and merges duplicate
entities (auto-merge / review / unmerge), person-scoped and same-kind, gated on
name/alias evidence — so two contributors who each create a same-named entity
(both "Amma") are already merged today. But:

1. The merge is **provenance-blind**: `_merge_entity_rows` copies name /
   description / aliases but **not `told_by_user_id`**, so the survivor keeps
   whichever row won — not necessarily the **first introducer** (#26).
2. A cross-contributor merge is **surfaced no differently** from a
   within-contributor one — Node can't tell the UI *"Priya's Amma = Ravi's
   Amma."*

SP6b fixes both. **Detection is unchanged** (name/alias, person-scoped) — no
different-surface-form ("Mom" = "Ishita") detection; that was explicitly
deferred (embedding distance stays verifier-context, never a trigger).

## Context discovered in this repo

- `flashback.identity_merges`: `scanner._find_candidates` pairs active same-kind
  entities on **name/alias** overlap only (it does not read `told_by`);
  `verifier` (small LLM) returns verdict + confidence; `disposition` routes
  `auto_merge | ask | drop`; `repository` applies merges + unmerge.
- `_merge_entity_rows` selects the source's `kind, name, description, aliases,
  attributes, generation_prompt` (not `told_by_user_id`), repoints edges onto
  the survivor (target), marks source `merged`, and returns an `undo_snapshot`
  (`source_row`, `repointed_edge_ids`, `deleted_edges`). The survivor's
  `told_by_user_id` is never set; it keeps the target's.
- `identity_merge_suggestions` (migration 0012/0024) holds the pending/auto
  review records (`source_entity_id`, `target_entity_id`, `reason`, `source`,
  `status`, `undo_snapshot`, `notification_text`, …). It has **no** provenance
  columns.
- Read surfaces: `list_suggestions_async` (`GET /identity_merges/suggestions`),
  `list_auto_merged_async` (`GET /identity_merges/auto_merged`); pydantic models
  `IdentityMergeSuggestion`, `AutoMergeNotification`.
- Entities carry `told_by_user_id` = **first introducer**, never restamped on
  reuse (#17a/#26). `collaborator_onboarding` resolves `told_by_user_id →
  display_name` (the JOIN used for moment/entity attribution, #20/#26).
- Latest migration is 0034. SP6b adds **0035**.

## Decisions

### D1 — Survivor carries the earliest introducer's provenance

On every merge, set the survivor's `told_by_user_id` to the `told_by_user_id`
of the **older** of the two merged entities (by `created_at`) — the contributor
who first introduced that identity. Creator-era `NULL` counts as a valid value
(if the older row is creator-era, the survivor becomes/stays `NULL`).

This is **merge-specific** and distinct from the reuse-fold "never restamp"
rule (#26): a merge collapses two identities into one, so the merged identity's
provenance is the earliest introducer's — even if that means rewriting the
survivor (target) row's `told_by_user_id` to the source's. On a `created_at`
tie (negligibly rare), the survivor keeps its own `told_by` (no rewrite).

### D2 — Unmerge restores provenance exactly

The `undo_snapshot` gains two fields: the **source's** `told_by_user_id` and the
**survivor's pre-merge** `told_by_user_id`. On unmerge: the resurrected source
gets its original `told_by_user_id` back; the survivor's `told_by_user_id`
reverts to its pre-merge value. Fully symmetric with D1.

### D3 — Capture both originals on the record; surface cross-contributor + names

A migration adds `source_told_by_user_id UUID NULL` and
`target_told_by_user_id UUID NULL` to `identity_merge_suggestions`, **captured at
record-creation time** (scanner suggestion + auto-merge), when both entities'
originals are still known. They must be captured (not resolved live post-merge)
because D1 rewrites the survivor's `told_by`, after which the original pair is
no longer recoverable from the live rows.

The read surfaces then expose, with display names resolved **live** via
`LEFT JOIN collaborator_onboarding` (NULL = creator era):

- `cross_contributor: bool` = `source_told_by_user_id IS DISTINCT FROM
  target_told_by_user_id` (so creator-era NULL vs a collaborator is also
  cross-contributor).
- `source_told_by_display_name`, `target_told_by_display_name`.

Node renders *"Priya's Amma and Ravi's Amma are the same person — merged"* when
`cross_contributor`; generic phrasing otherwise. These feeds are **per-legacy**
(`person_id`-scoped) and **audience-agnostic** — Node decides which member(s)
(usually the owner) see and act on them. Merges are graph-level corrections, so
they are **not** contributor-scoped like questions (#27).

### D4 — Scope

Applies to **both** the silent auto-merge path and the user-approved merge path.
Detection (candidate gate) is **unchanged**. The candidate query and
`IdentityMergeCandidate` gain `told_by_user_id` + `created_at` for both sides,
threaded into the merge (D1) and the suggestion insert (D3).

## Approaches considered (surfacing storage)

**A. Store both `told_by` ids on the suggestion row (chosen).** Captured at
creation when both originals are known; `cross_contributor` derived + names
resolved live. Reliable through D1's survivor rewrite. One small migration.

**B. Resolve everything live from the entity rows (rejected).** After a merge
the survivor's `told_by` is overwritten (D1) and the source row is `merged`, so
the original pair can't be reconstructed → false negatives on `cross_contributor`
exactly when the survivor adopted the source's provenance. Unreliable.

**C. Store only a `cross_contributor` boolean (rejected).** Cheaper, but the UI
can't name *who* without its own lookup; the chosen "flag + names" needs the ids
anyway.

## Design

### Migration `0035_identity_merge_provenance`

- `ALTER TABLE identity_merge_suggestions ADD COLUMN source_told_by_user_id UUID`,
  `ADD COLUMN target_told_by_user_id UUID` (both nullable, no FK — they mirror
  Node user ids like `moments.told_by_user_id`). No backfill (existing rows show
  `cross_contributor=false`, names NULL — acceptable for historical rows).
- `.down.sql` drops both columns.

### Scanner (`scanner.py`)

- `_find_candidates` SELECT adds `a.told_by_user_id, a.created_at,
  b.told_by_user_id, b.created_at`.
- `IdentityMergeCandidate` gains `source_told_by_user_id: str | None`,
  `target_told_by_user_id: str | None`, `source_created_at`, `target_created_at`.
- `_insert_scanner_suggestion` writes the two `*_told_by_user_id` columns.
- `auto_merge_async` is called with the two told_by ids + created_ats.

### Repository (`repository.py`)

- `_merge_entity_rows` gains the two created_ats + told_by ids (or reads them
  `FOR UPDATE` alongside the existing source/target selects). After repointing
  edges and marking source `merged`:
  - compute `survivor_told_by = older_by_created_at(source, target).told_by`,
  - `UPDATE entities SET told_by_user_id = survivor_told_by WHERE id = <survivor>`
    (no-op when the survivor is already the older row),
  - extend `undo_snapshot` with `source_told_by_user_id` and
    `survivor_prior_told_by_user_id`.
- `auto_merge_async` / `approve_merge_async`: persist `source_told_by_user_id` +
  `target_told_by_user_id` onto the suggestion row (auto-merge inserts the row;
  approve updates an existing row that already has them from the scanner).
- `unmerge_async`: resurrect the source with `undo_snapshot.source_told_by_user_id`;
  set the survivor's `told_by_user_id` back to
  `undo_snapshot.survivor_prior_told_by_user_id`.

### Read surfaces (`repository.py` + `schema.py`)

- `list_suggestions_async` + `list_auto_merged_async` SQL: select the two stored
  `*_told_by_user_id`, `LEFT JOIN collaborator_onboarding` twice for display
  names, and compute `cross_contributor` in SQL (`IS DISTINCT FROM`).
- `IdentityMergeSuggestion` + `AutoMergeNotification` gain
  `cross_contributor: bool`, `source_told_by_display_name: str | None`,
  `target_told_by_display_name: str | None` (defaults: `False`/`None`).

### Docs

- `API.md`: the new fields on the suggestion + auto_merged responses.
- `NODE_INTEGRATION.md` / `COLLABORATOR_NODE_INTEGRATION.md`: flip SP6b items
  from 🟡 to live; note `cross_contributor` + names + the per-legacy audience.
- CLAUDE.md: extend invariant #17 (or a note under #26) — merges preserve the
  earliest introducer's `told_by`; cross-contributor merges expose
  `cross_contributor` + names; migration 0035.

## Out of scope

- Different-surface-form detection ("Mom" = "Ishita") — detection stays
  name/alias; embedding distance remains verifier-context, never a trigger.
- Any change to the verifier / disposition policy.
- Backfilling provenance onto pre-0035 suggestion rows.
- Node-side UI work.

## Testing

- **Survivor provenance (D1, DB):** merging A (older, `told_by=Priya`) and B
  (newer, `told_by=Ravi`) → survivor `told_by=Priya`, for **both** source/target
  orderings (survivor adopts older's told_by even when older is the source);
  creator-era NULL older → survivor NULL.
- **Unmerge (D2, DB):** after merge, unmerge restores the resurrected source's
  original `told_by` and reverts the survivor's `told_by` to its pre-merge value.
- **Capture (D3, DB):** auto-merge + scanner-suggestion rows persist both
  `*_told_by_user_id`; values survive the survivor's D1 rewrite.
- **Surfacing (DB):** `cross_contributor` true when introducers differ (incl.
  creator-era NULL vs collaborator), false when same; display names resolve live
  from `collaborator_onboarding`; creator-era → NULL name.
- **Schema (unit):** new pydantic fields default `False`/`None`.
- **Non-regression:** existing identity-merge tests pass; detection unchanged.
