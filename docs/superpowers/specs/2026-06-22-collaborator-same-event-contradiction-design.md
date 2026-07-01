# Collaborator Feature — Sub-project 5: Same-Event Linking + Contradiction Review

**Date:** 2026-06-22
**Status:** Approved design, pre-implementation
**Parent strategy:** Collaborator feature Phase 1 ("Open and Attributed"). This is
the fifth of six sub-projects:

1. Provenance foundation — **done** (migration 0026/0027).
2. Per-contributor continuity + speaker-first retrieval + attribution — **done**.
3. Collaborator onboarding + onboarding phase — **done** (0028–0032).
4. Cross-contributor name recognition (lite) — **done**.
5. **Same-event linking + contradiction review items** ← this spec.
6. Cross-contributor identity merges + collaborator removal.

## Goal

Two contributors talking about the same person inevitably describe **the same
event** (a wedding, a birthday, a funeral) from their own angles, and sometimes
they **contradict** each other (he was 60, no he was 65). Today the agent has no
notion of either:

- Complementary accounts of one event are classified `independent` and stay
  disconnected — the agent can never say "your brother remembers this day too."
- Conflicting accounts are detected (`contradiction` verdict) but **only
  logged** ([persistence.py:257](../../../src/flashback/workers/extraction/persistence.py))
  — never persisted, never surfaced.

SP5 closes both gaps by adding one new compatibility verdict (`same_event`) and
two durable record tables, then wiring them into retrieval (links) and Node-side
review endpoints (both). It reuses the candidate search that already runs on
every new moment, so detection cost is near-zero.

## Context discovered in this repo

- **The detection seam already exists.** The Extraction Worker runs a vector +
  entity-overlap search for refinement candidates per new moment
  (`worker.py:668`), then calls `judge_compatibility`
  (`compatibility_llm.py`) per candidate. The verdict enum is
  `CompatibilityVerdict = Literal["refinement", "contradiction", "independent"]`
  (`schema.py:28`). The search is **person-scoped, contributor-agnostic** — it
  already pulls another contributor's moments as candidates.
- **`contradiction` is currently a dead end.** `worker.py:680` appends the
  candidate id to `decision.contradicts_ids`; `persistence.py:255-260` emits a
  `extraction.contradiction_logged` structlog line and nothing else. No row, no
  edge, no surface.
- **The lifecycle pattern to mirror is `identity_merges`.** That module
  (`scanner` → `verifier` → `disposition` → `repository` → HTTP) implements
  exactly the auto-action + notify + acknowledge + reverse pattern SP5 needs:
  silent auto-merge with a toast feed (`GET /identity_merges/auto_merged`,
  `POST /identity_merges/{id}/acknowledge`) and a reversal
  (`POST /identity_merges/{id}/unmerge`). SP5's same-event link reuses this
  shape; SP5's contradiction review reuses the pending-suggestion shape.
- **Supersession is a status flip, never an overwrite** (`_supersede_moment`,
  `persistence.py:1051`). The old moment row is kept (`status='superseded'`,
  `superseded_by=<new id>`) with its original `told_by_user_id` intact; the
  refined moment is a **new** row stamped with the refining segment's
  contributor (foundation D4 rule #4). Invariant #5 repoints all edges from the
  old id to the new id in the same transaction. SP5 records reference moment
  ids, so they must participate in this repointing (see D6).
- Latest migration is 0032. SP5 adds **0033**.

## Decisions

### D1 — New compatibility verdict: `same_event`

`CompatibilityVerdict` becomes
`Literal["refinement", "same_event", "contradiction", "independent"]`. The
compatibility tool enum, system prompt, and `worker.py` routing all gain the new
value. Verdict semantics:

| Verdict | Meaning | Action |
|---|---|---|
| `refinement` | same memory, newer adds detail / corrects | supersede old *(unchanged)* |
| `same_event` | **same event, both valid, complementary perspectives** | **auto-link both moments + notify** (D2) |
| `contradiction` | overlapping but cannot both be true | **persist a review item** (D3) |
| `independent` | unrelated; shared entity/theme only | nothing *(unchanged)* |

Prompt guidance: prefer `same_event` over `independent` only when the two
moments clearly describe **one shared occasion**; prefer `independent` when in
doubt (false links are noise, but cheaper than false refinements since both
moments survive). `same_event` and `contradiction` are **not** mutually
exclusive in principle, but the verdict is single-valued: when two accounts of
one event also conflict on a fact, the LLM returns `contradiction` (the conflict
is the more actionable signal). The worker takes the **first** `refinement`
match and stops (unchanged); for `same_event` and `contradiction` it records
**every** matching candidate (a new moment can be the same event as, or
contradict, more than one existing moment).

### D2 — Same-event links: auto-link + notify, reversible via unlink

On a `same_event` verdict the worker writes a row to **`moment_same_event_links`**
inside the existing persistence transaction. No review gate — additive and safe,
like the entity-reuse fold. The row carries an LLM-authored `reason` (the
compatibility `reasoning`) and `acknowledged_at = NULL`, so it appears in a
toast feed until dismissed. The user can **unlink** (status → `unlinked`) if the
link is wrong — the escape hatch that lets us defer the heavier "review before
linking" option unless false links prove common in testing.

### D3 — Contradiction review items: non-destructive record + dismiss

On a `contradiction` verdict the worker writes a row to **`moment_contradictions`**
(`status='pending'`) instead of the structlog line. This cycle the **only**
resolution is **dismiss** ("keep both / not a conflict") — `status='dismissed'`,
no graph mutation, both moments stay active. There is deliberately **no**
"pick canonical / supersede" action in v1: contradictions between two
contributors' memories are not the agent's to adjudicate, and a destructive
resolution risks erasing a valid account. (A future cycle may add canonical
selection; the table leaves room.)

### D4 — Agent consumes links only; contradictions are Node-only

- **Same-event links feed retrieval.** On `recall` intent (the only intent that
  searches moments, invariant #19), after the existing moment search the
  Retrieval Service also fetches **active** same-event-linked moments for the
  retrieved set and renders them in a `<linked_accounts>` block. The Response
  Generator may weave the other account in. The "**another contributor remembers
  this too**" framing reuses the **existing cross-contributor attribution guard**
  (render `told_by`/`relationship` only when the linked moment's
  `told_by_user_id` differs from the current user, mirroring `<mentioned_entities>`
  and `<moments>`). Within-contributor links render plainly (no attribution).
- **Contradictions never reach the agent.** They are purely a Node/UI review
  queue. The agent must not fact-check or raise discrepancies mid-conversation
  (anti-survey ethos, CLAUDE.md §1). No prompt sees contradiction data.

### D5 — Provenance is resolved live, never snapshotted

SP5 record rows store **only moment ids** (`moment_a_id`, `moment_b_id`) plus
`person_id`, `reason`, `status`, timestamps. They do **not** denormalize
`told_by_user_id` / display name. All read paths (retrieval join, GET endpoints)
resolve `told_by_*` by **JOINing `moments` live**, so attribution always reflects
the **current active** moment. This is required because supersession changes the
active row's `told_by_user_id` (D6 / foundation D4#4): a snapshot taken at
link-creation time would silently go stale and mislabel "who said it".

### D6 — Supersession repoints SP5 records (extends invariant #5)

When `_supersede_moment(old, new)` runs, it must also repoint SP5 records so they
never reference a superseded (inactive) moment:

- Any **active** `moment_same_event_links` row referencing `old` (as A or B) is
  repointed to `new`.
- Any **pending** `moment_contradictions` row referencing `old` is repointed to
  `new`.
- If repointing would make a row self-referential (`moment_a_id == moment_b_id`
  — e.g. `new` refined `old` and the partner side already equals `new`), that
  row is **collapsed**: same-event link → `status='unlinked'`; contradiction →
  `status='dismissed'` with a system note. A moment cannot be the same event as,
  or contradict, itself.

Repointing happens in the same transaction as the supersession, alongside the
existing edge repointing, and **re-canonicalizes** the A/B order after the new id
is substituted (so the smaller-UUID-first convention and the partial unique index
hold). The self-link collapse is a `status` flip, never an id UPDATE — the
`CHECK (moment_a_id <> moment_b_id)` constraint forbids writing equal ids.
`dismissed` / `unlinked` rows are **not** repointed (already terminal). The new
moment is **not** re-judged for compatibility against the old record's partner —
SP5 does not auto-re-detect on refinement; the repointed record stands until the
user acts on it.

## Approaches considered (same-event link storage)

**A. Dedicated table `moment_same_event_links` (chosen).** A reversible link
record with a notification lifecycle (`acknowledged_at`), an LLM `reason`, and a
status (`active`/`unlinked`) — exactly the `identity_merge_suggestions` shape.
Retrieval joins it. Mirrors a proven pattern; no churn to the `edges` enums or
`validate_edge`.

**B. New `same_event` edge meaning in the generic `edges` table (rejected).**
A same-event relationship is edge-like and would be repointed by invariant #5
for free. But `edges` has no column for ack state, the LLM reason, or an
`unlinked` lifecycle — we would need a companion record anyway, creating two
sources of truth. The repointing benefit is recovered cheaply by D6.

**C. Cluster/event entity (rejected — YAGNI).** Promote each detected event to a
first-class node with N moments attached. Powerful for "show the whole event"
UIs, but heavy: a new node kind, edge plumbing, and naming LLM. Flat pairwise
links are enough for retrieval ("pull the other accounts of moments in this
set"); if three contributors describe one event, pairwise links between the
overlapping pairs suffice.

## Design

### Migration `0033_same_event_and_contradictions`

```sql
CREATE TABLE moment_same_event_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES persons(id),
    moment_a_id     UUID NOT NULL REFERENCES moments(id),
    moment_b_id     UUID NOT NULL REFERENCES moments(id),
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'unlinked'
    acknowledged_at TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (moment_a_id <> moment_b_id)
);

CREATE TABLE moment_contradictions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES persons(id),
    moment_a_id     UUID NOT NULL REFERENCES moments(id),
    moment_b_id     UUID NOT NULL REFERENCES moments(id),
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'dismissed'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ NULL,
    CHECK (moment_a_id <> moment_b_id)
);
```

Indexes: `(person_id, status)` on both; `(moment_a_id)` and `(moment_b_id)` on
both (D6 repointing lookups). To collapse mirror duplicates (A–B vs B–A) we
canonicalize on insert — store the smaller UUID as `moment_a_id` — and add a
per-table partial unique index on `(moment_a_id, moment_b_id)` over each table's
**live** status:
`moment_same_event_links … WHERE status = 'active'` and
`moment_contradictions … WHERE status = 'pending'`. `.down.sql` drops both
tables.

### Module `flashback.moment_links` (new)

Mirrors `flashback.identity_merges` layout:

- `schema.py` — `SameEventLink`, `ContradictionItem` dataclasses/pydantic models.
- `repository.py` — async functions:
  - `insert_same_event_link(cur, *, person_id, moment_a_id, moment_b_id, reason)`
    (sync-cursor variant for the worker tx; canonicalizes A/B order, idempotent
    via the partial unique index).
  - `insert_contradiction(cur, *, person_id, moment_a_id, moment_b_id, reason)`.
  - `list_event_links(person_id, *, include_acknowledged=False)` — joins
    `moments` for live `told_by_*` (D5).
  - `acknowledge_event_link(link_id)`; `unlink_event_link(link_id)`.
  - `list_contradictions(person_id)` — pending; joins `moments` live.
  - `dismiss_contradiction(item_id)`.
  - `repoint_records_on_supersession(cur, *, old_id, new_id)` — D6, sync cursor,
    called from the worker tx.

### Extraction Worker wiring

- `compatibility_llm.py` / `prompts.py` / `schema.py`: add `same_event` to the
  verdict enum, tool enum, and prompt.
- `persistence.py` `MomentDecision`: add `same_event_ids: list[str]` alongside
  `contradicts_ids`. `worker.py` candidate loop routes `same_event` →
  `decision.same_event_ids.append(candidate.id)` and `contradiction` →
  `decision.contradicts_ids.append(candidate.id)` (record all, not first).
- `persistence.py` moment-persist step: after the new moment id is known, for
  each `same_event_ids` entry call `insert_same_event_link`; for each
  `contradicts_ids` entry call `insert_contradiction` (replacing the structlog
  line). Both inside the existing transaction.
- `_supersede_moment`: after repointing edges, call
  `repoint_records_on_supersession(cur, old_id=..., new_id=...)`.

### Retrieval Service

- New query `get_same_event_linked_moments(person_id, moment_ids)` →
  `list[MomentResult]`: for the given retrieved moment ids, return the **active**
  same-event-linked partner moments (active moments only), with `told_by_*`
  populated from the moments join (the `MomentResult` already carries these
  fields). De-duplicated against the already-retrieved set.
- `orchestrator/steps/retrieve.py`: on `recall` intent, after the moment search,
  populate `TurnContext.linked_account_moments` from this query (best-effort; a
  failure logs and yields an empty list — never blocks the turn).

### Response Generator

- `TurnContext` gains `linked_account_moments: list[MomentResult] = []`.
- `context.py` renders a `<linked_accounts>` block (only when non-empty), reusing
  the **same cross-contributor attribution guard** already used for `<moments>`
  and `<mentioned_entities>`: emit `told_by="…"`/`relationship="…"` only when the
  linked moment's `told_by_user_id` is non-null and differs from
  `ctx.current_user_id` and a display name is present.
- `prompts.py`: a short `BASE_SYSTEM_PROMPT` note — when a `<linked_accounts>`
  block is present, the agent may naturally reference that another contributor
  remembers the same occasion (crediting them when attributed), but must not
  force it or contradict the user.

### HTTP routes (`flashback/http/routes/`)

Node-driven, unauthed (§3), mirroring identity_merges routes:

- `GET  /event_links?person_id=…&include_acknowledged=false` — same-event feed.
- `POST /event_links/{id}/acknowledge` — dismiss toast. Idempotent.
- `POST /event_links/{id}/unlink` — reverse a same-event link (status →
  `unlinked`).
- `GET  /contradictions?person_id=…` — pending review items.
- `POST /contradictions/{id}/dismiss` — keep both / resolve (status →
  `dismissed`, stamp `resolved_at`).

Request/response shapes added to `API.md`; Node consumption notes (these are
agent-owned tables Node reads via the endpoints, never writes directly) added to
`NODE_INTEGRATION.md`.

### CLAUDE.md

Add invariant **#28** (same-event linking + contradiction review): the
`same_event` verdict, auto-link + notify + unlink, non-destructive contradiction
records, live provenance resolution (D5), and the supersession-repoint rule
(D6, extending #5). Note the new `moment_links` module and the two tables in §5.

## Out of scope (later / SP6)

- "Pick canonical / supersede" resolution for contradictions (D3 defers).
- Re-judging compatibility when a refinement repoints an SP5 record (D6 defers).
- A clustered "event" node (approach C, YAGNI).
- A backfill scan over moments extracted before SP5 (detection is live-only per
  the chosen approach; pre-SP5 moments simply never got the `same_event` check).
- Cross-contributor **identity** merges + collaborator removal — SP6.
- Any Node-side UI work.

## Testing

- **Verdict routing (unit):** each of the 4 verdicts maps to the correct
  persistence action; `same_event` and `contradiction` record **all** matching
  candidates, `refinement` takes the first and stops.
- **Persistence (DB-gated):** a `same_event` verdict writes one
  `moment_same_event_links` row (A/B canonicalized, `status='active'`,
  `acknowledged_at` NULL); a `contradiction` writes one `moment_contradictions`
  row (`status='pending'`); the partial unique index makes re-detection
  idempotent; both written inside the moment transaction.
- **Supersession repoint (DB-gated, D6):** superseding a linked moment repoints
  the active link to the new id; superseding into a self-link collapses the row
  (`unlinked` / `dismissed`); terminal rows are not repointed.
- **Live provenance (D5):** GET endpoints and the retrieval join return the
  **current active** moment's `told_by_*`; after a supersession that changes the
  active teller, the resolved attribution follows.
- **Retrieval (DB-gated):** `get_same_event_linked_moments` returns active
  partners only, de-duped against the retrieved set; unlinked links excluded.
- **Render (unit):** `<linked_accounts>` renders with `told_by=` only for
  cross-contributor links, plainly for own/null/within-contributor; absent when
  empty.
- **Endpoints (DB-gated):** list/acknowledge/unlink/dismiss happy paths +
  idempotency + 404 for wrong person.
- **Non-regression:** existing contradiction-logging test updated to assert the
  row write; all other tests pass unchanged. No-DB baseline and DB-gated baseline
  unchanged otherwise (additive).
