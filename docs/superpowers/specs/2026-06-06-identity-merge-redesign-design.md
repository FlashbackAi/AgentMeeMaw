# Identity Merge Redesign — Prevention + Smart Reconcile

**Date:** 2026-06-06
**Status:** Design — approved for planning
**Scope:** Python agent service (this repo) only. No Node changes; Node-facing
changes are limited to new/changed HTTP endpoints it reads/calls.

---

## 1. Problem

The `identity_merge_suggestions` queue for a single legacy
(`a5a46b18-7b10-49f5-8920-fbadeb52c051`) is flooded with low-quality,
duplicated, and outright wrong merge suggestions:

- *"Both rows are named 'Ishita'."* — repeated dozens of times.
- *"'Aarav' appears in the details for 'Ishita'."* — co-occurrence treated as
  identity evidence.
- *"'Mokshith' appears in the details for 'Mokshith's mother'."* — a person
  matched to their own relative.
- *"Old Tools" ↔ "The Treehouse"* — two **different-kind** entities
  (object vs place) flagged because one appeared in the other's description.

Two root causes:

1. **Extraction re-mints entities every segment.** The extraction LLM is never
   shown which entities already exist for the person, so it coins a fresh
   "Ishita"/"Aarav"/"Comet" row each time. Every duplicate row spawns its own
   `artifact_generation` job (wasted compute) **and** a fresh merge suggestion
   against every prior duplicate. The merge queue is a *symptom*; duplicate
   creation is the disease.

2. **Two un-gated, brute-force suggestion paths.** Both
   `flashback.identity_merges.repository.create_entity_merge_suggestions`
   (called from the Extraction Worker) and
   `flashback.node_edits._async_sql.create_entity_merge_suggestions_async`
   (called from node-edit refinement) write suggestions directly from pure
   string matching — same name, **or the name appears as a substring of the
   other row's description** — with **no LLM verification**. The
   substring-in-description rule is the false-positive engine. The only
   LLM-gated path (`flashback.identity_merges.scanner` +
   `flashback.identity_merges.verifier`) is invoked manually via
   `POST /identity_merges/scan` and is drowned out.

## 2. Goals

- **Stop creating duplicate entity rows at the source** (and the wasted
  artifact jobs that ride on them).
- **Replace brute-force suggestions** with a single verifier-gated pipeline
  whose candidates rest on real name/alias evidence — never co-occurrence,
  never cross-kind.
- **Auto-merge only when near-certain**, and notify the user with a clear,
  human reason and a one-click unmerge.
- **Make unmerge safe and exact** — reversible without mangling the survivor.
- Apply uniformly to **all four entity kinds** (`person`, `place`, `object`,
  `organization`).

## 3. Non-goals (explicitly out of scope)

- **Backfill / cleanup of existing data.** The dozens of pending
  Ishita/Aarav rows and the already-duplicated entity rows in
  `a5a46b18-…` are **not** touched by this work. A separate throwaway
  script will collapse legacy duplicates and reprocess the existing pending
  backlog later. This spec is **go-forward only**.
- Voice/photoreal/likeness concerns — unrelated.
- Multi-contributor identity reconciliation — deferred with `person_roles`.

## 4. Locked decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Where the fix sits | **Both** prevention (at source) + smart reconcile (backstop) |
| Auto-merge risk appetite | **Conservative**: auto-merge only on `same_identity` + `high` |
| Reversibility model | **Snapshot + exact reverse**; survivor stays intact, merged-away entity resurrected as a fresh standalone entity |
| Candidate trigger | **Name/alias evidence only** (Option A). Substring-in-description deleted; embedding is LLM *context only*, never a trigger |
| Different-name dups ("Mom" = Ishita) | Handled **upstream at extraction** with conversation context (entity catalog), not by an embedding trigger |
| Reason text | **LLM-authored** specific one-liner; template strings only as error fallback |
| Entity kinds | **All four**, same-kind constraint enforced |
| Existing data | **Go-forward only** (no backfill) |

---

## 5. Architecture

Two layers of prevention upstream; one verifier-gated reconcile layer as a
backstop downstream.

```
EXTRACTION
  │
  ├─ Layer 2 (LLM): <entity_catalog> in prompt → reuse existing entity
  │                  when a mention refers to a known one
  │
  └─ PERSISTENCE
       ├─ Layer 1 (deterministic): normalized name/alias + same-kind match
       │   to an existing ACTIVE entity → reuse its id, fold aliases,
       │   skip insert, skip artifact job, no suggestion
       │
       └─ For entities that are genuinely new → insert as today
                  │
                  ▼
          RECONCILE BACKSTOP (verifier-gated)
            candidate gate (name/alias evidence, same-kind only)
                  │
                  ▼  small-LLM verifier (embedding distance = context)
            ┌─────────────┬──────────────────┬──────────────────┐
            ▼             ▼                  ▼
   same_identity+high   same_identity+med    different / low /
   → AUTO-MERGE         or unsure            cross-kind
     + notify + undo    → ASK (pending)      → DROP (no row)
```

### 5.1 Prevention layer 1 — deterministic name resolution (persistence)

In the extraction persistence path (and the node-edit persistence path),
**before inserting** a freshly-extracted entity:

1. Normalize the candidate name (`strip().lower()`, collapse whitespace).
2. Query for an existing **active, same-kind** entity for this `person_id`
   whose normalized `name` matches, OR whose `aliases` contains the candidate
   name (case-insensitive), OR whose `name` matches one of the candidate's
   emitted aliases.
3. On match:
   - **Do not insert a new row.** Resolve this segment's edges (involves,
     happened_at, etc.) to the existing entity id.
   - Fold any new aliases / description additions into the existing row
     (same alias-merge + description-merge helpers used by
     `_merge_entity_rows`). If the description changes, NULL the embedding
     columns and push an `embedding` job (invariant #3/#4).
   - **No `artifact_generation` job** (no new artifact-bearing row).
   - **No merge suggestion** — this is a silent, deterministic reuse.
4. On no match: insert as today.

This is the single highest-leverage change: it eliminates the same-name flood
and its wasted artifact jobs entirely, deterministically, no LLM. Consistent
with CLAUDE.md §10 ("code over LLM for orchestration").

**Ambiguity guard:** if **two or more** existing active same-kind entities
match the normalized name (i.e. pre-existing legacy duplicates), Layer 1 does
**not** guess — it resolves to the oldest (lowest `created_at`) match and lets
the reconcile backstop clean up the rest. (Pre-existing duplicates are
out-of-scope to backfill, but Layer 1 must not crash or double-resolve on
them.)

### 5.2 Prevention layer 2 — entity catalog into the extraction LLM

Build a per-person catalog of **active** entities (`id`, `name`, `aliases`,
`kind`, short `description`) and render it as `<entity_catalog>` in the
extraction user message, mirroring how `<theme_catalog>` is already built and
injected (`worker.py` `_build_theme_catalog` → `theme_catalog=` arg).

- Source: reuse the same Postgres read that backs the entity-mention scanner
  catalog (invariant #20), but **include all kinds** here (the #20 object
  exclusion is specific to the mention scanner and does not apply to
  extraction dedup).
- Prompt instruction: when a mention clearly refers to a catalog entry, reuse
  that entity's canonical name (and add the new surface form as an alias)
  rather than coin a new label. This resolves **different-name** identities
  ("Mom" → "Ishita") using conversation context — the right place for it.
- Best-effort: if the LLM coins a new name regardless, Layer 1 still catches
  exact/alias matches deterministically. Layer 2 is the only mechanism for
  same-identity / different-surface-name, but it is not load-bearing for
  correctness — a miss just falls to the reconcile backstop.

### 5.3 Reconcile backstop — candidate gate

Consolidate candidate generation into the verifier-gated path. The two
un-gated direct-insert functions
(`identity_merges.repository.create_entity_merge_suggestions` and
`node_edits._async_sql.create_entity_merge_suggestions_async`) **no longer
write suggestions**.

**Invocation (as built).** Prevention layer 1 runs inline and
automatically on every extraction/edit commit (deterministic, no LLM),
which silently resolves the dominant same-name duplicate case. The
LLM-gated reconcile (candidate gate → verifier → disposition) is exposed
via `POST /identity_merges/scan` for Node/cron to trigger; it was kept
off the per-segment extraction critical path deliberately, since
prevention layer 1 already eliminates same-name duplicates there and a
per-segment small-LLM pass would mostly find nothing. Wiring an
automatic post-extraction reconcile into the sync worker (via the
established `asyncio.run` bridge) is a clean follow-up if desired.

Candidate gate (`scanner._find_candidates`) triggers **only** when, for two
active entities of the **same kind** under the same `person_id`:

- `normalize(a.name) == normalize(b.name)`, OR
- `a.name` ∈ `b.aliases` (case-insensitive), OR
- `b.name` ∈ `a.aliases` (case-insensitive).

**Deleted:** the `position(lower(a.name) in lower(b.description))` substring
clauses (both directions). Co-occurrence in a description is never identity
evidence.

**Never triggers:** cross-kind pairs; embedding distance alone.

Embedding distance is still **computed** (when both rows have
`description_embedding`) and passed to the verifier as supporting context, but
it is not part of the trigger condition.

**Applies to all entity kinds.** `object`/`place`/`organization` duplicates go
through the identical gate. The same-kind constraint is what prevents
"Old Tools" (object) ↔ "The Treehouse" (place) from ever forming a candidate,
regardless of shared moments.

### 5.4 Reconcile backstop — verify → disposition

Each candidate is verified by the small-LLM verifier
(`identity_merges.verifier`), which returns `verdict` ∈
{`same_identity`, `different_identity`, `unsure`} and `confidence` ∈
{`low`, `medium`, `high`} and a `reasoning` string.

| verdict + confidence | disposition |
|---|---|
| `same_identity` + `high` | **AUTO-MERGE** silently; write notification + undo snapshot (status `auto_merged`, `acknowledged=false`) |
| `same_identity` + `medium`, OR `unsure` (any confidence) | **ASK** — pending suggestion (status `pending`) with LLM-authored reason |
| `different_identity`, OR `same_identity`+`low`, OR cross-kind | **DROP** — no row written |

Edge case (resolved in `disposition.decide_disposition`): `unsure` always
**asks** (it means "needs a human") regardless of confidence; `low` only
drops when paired with `same_identity` (a hint too weak to even ask
about); `different_identity` always drops.

**LLM-authored reason text.** The verifier prompt is tightened so `reasoning`
is a single clean, specific, user-facing sentence (e.g. "You called him Aarav
in March and again last week — they look like the same person"). That string
is surfaced verbatim on both the ask card (`reason`) and the auto-merge
notification (`notification_text`). The existing robotic template strings
(`_suggestion_reason`, `_reason`) are retained **only** as a fallback when the
verifier call errors.

### 5.5 Auto-merge, notification, and reversible unmerge

**Auto-merge** reuses `_merge_entity_rows` / `_repoint_entity_edges`, but
**first** captures an `undo_snapshot` JSONB:

- The full source entity row: `name`, `kind`, `description`, `aliases`,
  `generation_prompt`, `image_url`/`thumbnail_url` (read-only capture, never
  written by us), `embedding_model`, `embedding_model_version`, `attributes`,
  `created_at`, etc.
- **Every edge touching source** before the merge — both the edges
  `_repoint_entity_edges` will repoint AND the duplicate edges it will
  DELETE. Deleted edges are not recoverable from the survivor, so the full
  edge tuples (kind, ids, edge_type, attributes) must be stored to recreate
  them on unmerge.

Then the standard merge runs (source → `status='merged'`, `merged_into` =
survivor; edges repointed atomically per invariant #5; survivor embedding
nulled + re-embed job pushed). A suggestion row is inserted with
`status='auto_merged'`, `confidence`, `acknowledged=false`, `undo_snapshot`,
`notification_text`, `auto_merged_at=now()`.

**Notification** stays within service boundaries — we never call Node and
never touch DynamoDB. Node polls `GET /identity_merges/auto_merged?person_id=…`
(rows where `status='auto_merged' AND acknowledged=false`) to render a toast
with `notification_text`, then calls `POST /identity_merges/{id}/acknowledge`
to clear it.

**Unmerge** (`POST /identity_merges/{id}/unmerge`): in one transaction —

1. **Survivor stays intact.** We do **not** un-blend the survivor's aliases or
   description. (Minor alias retention is accepted as the cost of safety.)
2. **Resurrect** the merged-away entity as a **fresh active entity** from the
   snapshot (new behavior: a `merged` row whose `merged_into` points at the
   survivor is restored to `status='active'`, `merged_into=NULL`, with its
   snapshotted scalar fields; embedding columns nulled).
3. Re-point the snapshotted repointed edges back to the resurrected entity,
   and re-insert the snapshotted deleted edges.
4. Push an `embedding` job for the resurrected entity (invariant #3/#4).
5. Flip the suggestion row to `status='unmerged'`, `unmerged_at=now()`.

A merge → unmerge round-trip must yield a graph where the resurrected entity
and its edges match the pre-merge state, and the survivor is unchanged from
its post-merge state.

---

## 6. Data model changes

### Migration 0024 — `identity_merge_suggestions`

- Extend `status` CHECK to: `('pending','approved','rejected','auto_merged','unmerged')`.
- Add columns:
  - `confidence TEXT NULL CHECK (confidence IN ('low','medium','high'))`
  - `acknowledged BOOLEAN NOT NULL DEFAULT false`
  - `undo_snapshot JSONB NULL`
  - `notification_text TEXT NULL`
  - `auto_merged_at TIMESTAMPTZ NULL`
  - `unmerged_at TIMESTAMPTZ NULL`
- Add partial index for the notification feed:
  `(person_id, acknowledged) WHERE status='auto_merged'`.
- CHECK additions mirroring existing timestamp guards:
  `auto_merged` ⇒ `auto_merged_at NOT NULL`; `unmerged` ⇒ `unmerged_at NOT NULL`.
- `0024_*.down.sql` reverses all of the above.

No changes to `entities` are required — `status` already supports `merged`
and the resurrect path reuses `active`; `merged_into` already exists.

---

## 7. HTTP surface (Node-facing)

New / changed endpoints (all unauthenticated; Node is the auth boundary):

- `GET /identity_merges/auto_merged?person_id=…` — list `auto_merged`,
  `acknowledged=false` rows for the toast feed (id, source/target names,
  `notification_text`, `auto_merged_at`).
- `POST /identity_merges/{id}/unmerge` — reverse an auto-merge (or an
  approved merge) per §5.5. Returns the resurrected entity id. 404 if the
  suggestion is not in a reversible state.
- `POST /identity_merges/{id}/acknowledge` — set `acknowledged=true` on an
  `auto_merged` row (toast dismissed). Idempotent.

Existing endpoints retained:
`GET /identity_merges/suggestions`, `POST /identity_merges/scan`,
`POST /identity_merges/suggestions/{id}/approve`,
`POST /identity_merges/suggestions/{id}/reject`.

`API.md` and `NODE_INTEGRATION.md` updated accordingly.

---

## 8. Invariant / doc updates

- **Invariant #17 amended.** Today it reads "Extraction … must not directly
  merge entities." This work changes that: extraction may now **auto-merge on
  high-confidence verifier verdict** (with notification + reversible unmerge),
  in addition to proposing pending suggestions for medium/unsure. The CLAUDE.md
  text is updated to describe the prevention layers, the disposition tiers, the
  same-kind constraint, and the unmerge contract.
- Honored unchanged: #1 (`status='active'` filtering on every query), #2
  (`person_id`-scoped; never cross-legacy), #3/#4 (embeddings via queue,
  model/version tracked, re-embed on change), #5 (edge repoint atomic — both
  merge and unmerge in a single tx), #6 (under-extract; `unsure`/`low` never
  auto-merge), #8 (no auth). We touch only agent-owned tables; never S3 / URL
  columns / DynamoDB.
- Docs to update in the same change (§10 "update all three together"):
  `CLAUDE.md`, `ARCHITECTURE.md`, `SCHEMA.md`, `API.md`, `NODE_INTEGRATION.md`.

---

## 9. Testing

- **Candidate gate regressions** (the exact reported false positives):
  - "Mokshith" (person) vs "Mokshith's mother" (person) → **no candidate**
    (was substring-in-description).
  - "Old Tools" (object) vs "The Treehouse" (place) → **no candidate**
    (cross-kind).
  - co-occurrence of "Aarav" inside "Ishita"'s description → **no candidate**.
  - Same normalized name + same kind → candidate formed.
- **Disposition tiers:** verifier verdict/confidence → correct
  auto-merge / ask / drop routing; `unsure` and `low` never auto-merge;
  `different_identity` writes nothing.
- **Prevention layer 1:** extracting an entity whose normalized name matches
  an existing active same-kind row → **no new entity row**, **no
  `artifact_generation` push**, **no suggestion**, edges resolved to the
  existing id, aliases folded, embedding re-queued on description change.
  Two-match ambiguity resolves to oldest without error.
- **Prevention layer 2:** `<entity_catalog>` is rendered into the extraction
  user message with all kinds; prompt reuse path exercised.
- **Merge → unmerge round-trip:** resurrected entity + its repointed and
  re-inserted edges match the pre-merge graph; survivor untouched relative to
  its post-merge state; re-embedding jobs pushed.
- **Notification feed:** `auto_merged`+`acknowledged=false` surfaces;
  acknowledge clears it; unmerge flips status to `unmerged`.

---

## 10. Risks & mitigations

- **Layer 1 over-merges a genuinely distinct same-name same-kind entity**
  (e.g. two different friends both named "Aarav"). Mitigation: the conservative
  posture accepts this as rare; the silent reuse is still reversible later via
  manual tooling, and the LLM catalog (Layer 2) can disambiguate when the
  conversation makes the distinction clear. Documented as accepted risk.
- **Undo snapshot drift** (edges added to the survivor after auto-merge that
  originally belonged to source can't be cleanly separated). Mitigation:
  unmerge restores exactly the snapshotted edges and leaves later edges on the
  survivor; survivor "stays intact" by design, so no data is lost — only the
  resurrected entity may miss post-merge edges. Accepted per the chosen
  unmerge model.
- **Verifier latency/cost** now on the extraction/edit critical path.
  Mitigation: candidate gate is name/alias-only and same-kind, so the verifier
  fires on a small set; runs after commit (not blocking the user turn).
