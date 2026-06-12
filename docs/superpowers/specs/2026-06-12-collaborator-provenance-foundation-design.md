# Collaborator Feature — Sub-project 1: Provenance Foundation

**Date:** 2026-06-12
**Status:** Approved design, pre-implementation
**Parent strategy:** Collaborator feature Phase 1 ("Open and Attributed") — see the
phase-wise strategy document. This is the first of six sub-projects:

1. **Provenance foundation** ← this spec
2. Per-contributor continuity + speaker-first retrieval + attribution
3. Collaborator onboarding (2-question modal, skip-tolerant nudges)
4. Cross-contributor name recognition (lite)
5. Same-event linking + contradiction review items
6. Cross-contributor identity merges + collaborator removal

## Goal

Every contributor-authored row in the canonical graph records which Node user
authored it. This sub-project is **write-path only**: no read-path changes, no
attribution rendering, no retrieval bias, no user-visible behavior change. It
ships as a no-op into the current single-contributor product; sub-projects 2–6
read the columns this one populates.

## Context discovered in this repo

- `role_id` (UUID) already arrives on every `/session/start` and `/turn` and is
  stored in Working Memory. It is **not** used for provenance (decision below).
- `contributor_display_name` already flows request → Working Memory →
  extraction queue payload → extraction prompts → response generator. It is
  used for attribution phrasing but never persisted.
- The extraction queue payload (`flashback/queues/extraction.py`) carries the
  display name but no identity field. Moments have no `told_by` column.
- No collaborator work has ever landed in this repo. Latest migration is 0025.

## Decisions

### D1 — Provenance identifier is the Node `user_id`

Per the parent strategy doc. Node adds `user_id: UUID` to the request bodies of
`/session/start`, `/turn`, and both stream variants. The agent stores it as
provenance. `role_id` remains in the contract and in Working Memory, accepted
and stored as today, but is **not** provenance.

Accepted trade-offs (from the strategy doc): re-invite "fresh start" relies on
status flips on old moments; GDPR account deletion becomes a cross-table scrub
of user ids; the agent DB holds Node user identifiers.

### D2 — `user_id` is optional in the contract; NULL means "creator era"

`user_id: UUID | None = None` so nothing breaks before Node ships its side.
**No backfill.** Every pre-collaborator row has `told_by_user_id IS NULL`, and
all future read paths (sub-projects 2–6) treat NULL as "told by the creator" —
correct because every existing legacy has exactly one contributor.

### D3 — Scope: contributor-authored tables only, with "first authored by" semantics

| Table | Columns added | Semantic | Stamped by |
|---|---|---|---|
| `moments` | `told_by_user_id UUID NULL`, `told_by_display_name TEXT NULL` | told by — load-bearing | Extraction Worker, every insert incl. supersession rewrites |
| `entities` | `told_by_user_id UUID NULL` | first introduced by — informational; lets the future cross-contributor merge flow classify a merge | Extraction Worker, **fresh inserts only** (deterministic reuse-folds do not restamp) |
| `traits` | `told_by_user_id UUID NULL` | first asserted by — cross-session merge-updates keep the original author | Extraction Worker, **fresh inserts only** |
| `questions` | `told_by_user_id UUID NULL` | whose session motivated it; NULL = system/seeded | Extraction Worker (inline P1), per-session producers (P2/P3/P5 when running with session context) |
| `profile_facts` | `told_by_user_id UUID NULL` | whose session produced the answer; user edits via Node carry the editing user when Node supplies it | Profile Summary worker; `/profile_facts/upsert` accepts optional `user_id` |
| `processed_extractions` | `told_by_user_id UUID NULL` | which user the segment belonged to — enables sub-project 3's first-moment flip | Extraction Worker |

**Not tagged:** `threads`, `themes`, profile summary fields on `persons` —
derived aggregates that span contributors by construction. Coverage-tap seed
questions, P4 thread-deepen questions, and any producer run without a session
user stamp NULL.

### D4 — Hard rules

1. **Only `moments.told_by_user_id` ever drives hiding/removal.** Entity,
   trait, question, and profile-fact tags are informational. Entities stay
   alive when their introducer is removed (strategy doc §2.13).
2. **Display name is denormalized only on `moments`** — the one place Phase 1
   renders attribution. Other tables hold the user id only.
3. **The LLM never sees or emits provenance.** Stamping is code-side, in
   persistence (Code over LLM, CLAUDE.md §10). Prompts are unchanged.
4. **Supersession preserves authorship of the refining segment**: a refined
   moment is stamped with the provenance of the segment that produced the
   refinement, not the original moment's.

## Approaches considered

**A. Thread-through (chosen).** `user_id` enters on the HTTP request, lives in
Working Memory beside `role_id`/`contributor_display_name`, rides the
extraction queue payload, and persistence stamps it at insert. Mirrors exactly
how `contributor_display_name` already flows; one source of truth per session.

**B. Session registry (rejected).** A Postgres session→user mapping the worker
consults at extraction time. Adds a table, a write path, and a failure mode to
deliver a fact the queue payload carries for free; drifts toward duplicating
Node-owned session data (§3 boundary).

## Design

### Contract (API.md, NODE_INTEGRATION.md)

- `SessionStartRequest`, `TurnRequest`, and the stream request models gain
  `user_id: UUID | None = None`.
- `ProfileFactUpsertRequest` gains optional `user_id`.
- Docs updated to state: `user_id` is the authoring Node user, required for
  multi-contributor legacies, omitted/null tolerated for single-contributor.

### Working Memory

- `WorkingMemoryState` gains `user_id: str = ""` beside `role_id`.
- Hydrated at `/session/start`, serialized in the HSET mapping, surfaced on the
  state object the orchestrator steps read. Ephemeral as always (invariant #7).

### Extraction queue payload

- `ExtractionQueueProducer.push` gains `told_by_user_id: str | None = None`.
- Both push sites (segment-boundary detect and session-wrap force-close) read
  it from Working Memory state alongside the display name they already pass.

### Migration `0026_contributor_provenance`

- Columns per D3 table above, all `NULL`-able, no defaults, no backfill.
- Partial index: `moments (person_id, told_by_user_id) WHERE status = 'active'`
  — the exact filter shape for speaker-first retrieval (sub-project 2) and
  removal (sub-project 6). No indexes on the other tables yet.
- `.down.sql` drops the index and columns.

### Extraction Worker

- Payload schema gains `told_by_user_id`.
- Persistence stamps `told_by_user_id` + `told_by_display_name` on every moment
  insert (display name from the payload's existing `contributor_display_name`).
- Entity persistence stamps `told_by_user_id` on fresh inserts only; the
  deterministic-reuse path (invariant #17a) folds aliases without restamping.
- Trait persistence stamps fresh inserts only; the merge-update path (#18b)
  leaves the column untouched.
- Inline P1 question inserts stamp `told_by_user_id`.
- `mark_processed` records `told_by_user_id` on `processed_extractions`.

### Producers and Profile Summary

- Per-session producer runs (P2/P3/P5 triggered from session wrap) receive the
  session's `user_id` through the existing fan-out payloads and stamp it on
  emitted questions. Cadence/weekly runs without a session user stamp NULL.
- The Profile Summary worker stamps `told_by_user_id` on `profile_facts` rows
  it writes, from the session context it already receives.
- `POST /profile_facts/upsert` stamps the request's `user_id` when present.

### Out of scope (later sub-projects)

- Reading `told_by_*` anywhere: attribution rendering, retrieval ranking,
  continuity scoping, conflict detection, merges, removal.
- The `collaborator_onboarding` table and onboarding orchestrator step.
- Any Node-side work (invite flow, membership rows, sending `user_id`).

## Testing

- **Request models:** accept `user_id`, default to `None`; reject malformed
  UUIDs (existing pydantic behavior).
- **Working Memory:** `user_id` round-trips through initialize → hydrate →
  serialize.
- **Queue payload:** `push` includes `told_by_user_id`; both push sites pass it
  from WM state; omitted → `null` in payload.
- **Extraction persistence:** moments stamped with id + display name; NULL
  stamped when payload lacks the field; entity reuse-fold does not restamp;
  trait merge-update does not restamp; fresh entity/trait/P1-question inserts
  are stamped; `processed_extractions` row carries the id.
- **Supersession:** refined moment carries the refining segment's provenance.
- **Producers/profile facts:** session-context runs stamp; cadence runs stamp
  NULL; upsert endpoint stamps when `user_id` supplied.
- All existing tests must pass unchanged — this sub-project is additive.
