# Collaborator Feature — Node-side Integration Checklist (SP1–SP6b)

**Audience:** the Node.js Backend team. This is the complete list of what Node
must add or handle to support Flashback's multi-contributor ("Collaborator
Phase 1 — Open and Attributed") feature, organised by sub-project. The Python
agent service owns the canonical graph and exposes the contract below; Node
owns auth, users, sessions, the UI, and membership, and calls these endpoints.

Detailed request/response shapes live in `API.md`; transport/async/boundary
notes in `NODE_INTEGRATION.md`. This file is the **Node-side to-do**, not the
agent internals.

**Status legend:** ✅ live in the agent · 🟡 designed, ships with SP6b.

---

## 0. The boundary (what Node owns vs the agent)

| Node owns | Agent owns |
|---|---|
| Auth & users; the **identity** behind `user_id` | The canonical Postgres graph (moments, entities, …) |
| Sessions + per-turn transcript (DynamoDB) | Working Memory (Valkey), all graph writes |
| **Membership** + DynamoDB `onboarding_complete` | The `collaborator_onboarding` mirror (agent-internal) |
| All user-facing **reads** + UI rendering | The review/notification **feeds** (merge, event-link, contradiction) |
| Showing the connection modal | Inferring relationship from conversation |
| Deciding **who** sees a notification | Producing per-legacy notifications (audience-agnostic) |

The agent has **no auth and no concept of a target user** — every notification
feed is keyed by `person_id` (the legacy/subject). **Node decides which
member(s) may see or act on each one** (typically the creator/owner).

---

## 1. Cross-cutting — applies to every request (foundation: SP1)

1. **Send `user_id: UUID` on every call** that carries a contributor:
   `POST /session/start`, `POST /turn`, and the SSE twins
   `POST /session/start/stream`, `POST /turn/stream`. It is the **authoring
   Node user**. It is the *only* identity field.
   - Optional during the transition: omitting it / `null` = **"creator era"**
     (treated as the single original contributor). Ship it as soon as possible
     for multi-contributor legacies, or provenance can't be attributed.
2. **Drop `role_id`.** It is retired. The agent tolerates it (won't 422) during
   the transition but ignores it entirely; remove it once you ship `user_id`.
3. **`POST /profile_facts/upsert`** accepts an optional `user_id` (the editing
   user) — send it so the edited fact is stamped with provenance.
4. **Auth headers** (Node is the auth boundary):
   - All endpoints: `X-Service-Token`.
   - `POST /admin/*` additionally requires `X-Admin-Service-Token` (a **distinct**
     token from the service token).
5. **Async timing.** Extraction runs asynchronously after `/session/wrap`. The
   review/notification feeds below populate **after** extraction commits — poll
   them (or react to the extraction-complete signal Node already consumes), not
   synchronously on the turn.

---

## 2. SP2 — Speaker-first retrieval + attribution

No new endpoint. Just ensure:
- `user_id` is sent (§1) — the agent scopes retrieval/continuity to the speaker
  and credits other contributors' moments.
- `contributor_display_name` is sent in `session_metadata` at `/session/start`
  (used for "Ravi told us…" attribution). See §3.

---

## 3. SP3 — Collaborator onboarding (modal + mirror)

The agent runs a lightweight per-`(person_id, user_id)` onboarding phase and an
in-chat nudge. Node's responsibilities:

1. **Show the connection modal** (frontend) for a new collaborator — this is a
   **Node/frontend responsibility**; the agent never asks the relationship
   directly in chat.
2. **Mirror the modal result to the agent** in `session_metadata` on
   `POST /session/start` (the agent upserts it into its onboarding mirror):
   | `session_metadata` key | meaning |
   |---|---|
   | `contributor_display_name` | the contributor's display name (e.g. "Ravi") |
   | `voice_anchor_text` | their relationship to the subject (e.g. "his daughter") |
   | `voice_anchored_at` | ISO-8601 timestamp (optional; agent defaults to session start) |
   | `modal_answered_at` | ISO-8601 — set when they answered the modal |
   | `modal_dismissed_at` | ISO-8601 — set when they dismissed it |
   Send whichever you have; all are optional and **never clobbered with NULL**.
3. **Keep owning DynamoDB `onboarding_complete`** — it is the **membership gate**.
   The agent never reads or writes it; the agent's onboarding phase is a
   separate, internal signal that only drives the in-chat nudge.

---

## 4. SP4 — Question scoping (provenance + scope gated)

No new endpoint and no new Node action beyond §1 (`user_id`). The agent
LLM-labels each produced question `public | personal | private` and SQL-gates
which contributor sees it. Node continues to render the producer-bank question
chips and POST chip decisions on the next `/turn` (`question_decision`) exactly
as before.

---

## 5. SP5 — Same-event linking + contradiction review ✅

Two agent-owned review surfaces Node consumes (never writes directly). Both
resolve `told_by_*` live, so attribution is always current.

**Same-event links** (auto-created; toast + reversible unlink):
- `GET  /event_links?person_id=…&include_acknowledged=false` — feed of links;
  each carries both moment titles + `told_by_*_display_name`.
- `POST /event_links/{id}/acknowledge` — dismiss the toast.
- `POST /event_links/{id}/unlink` — "these aren't the same event."

**Contradictions** (review queue; non-destructive):
- `GET  /contradictions?person_id=…` — pending items (both accounts + live
  `told_by_*`).
- `POST /contradictions/{id}/dismiss` — "keep both." (No "pick a winner" this
  cycle — both moments always coexist.)

Node renders the same-event toast and the contradiction review list to the
appropriate member(s). The agent never raises contradictions in chat.

---

## 6. SP6a — Collaborator removal (reversible) ✅

Offboard or restore a contributor. **Reversible hide — never a delete.**
- `POST /collaborators/remove` — body `{person_id, user_id}`. Hides that
  contributor's moments + the entities orphaned to them; everything else stays.
  Returns counts. **Idempotent** (unknown/already-removed → zero counts, 200).
- `POST /collaborators/restore` — body `{person_id, user_id}`. Exact inverse.

Node behaviour:
- To **bring someone back**, choose: **restore** the same `user_id` (content
  returns intact), or **fresh start** — re-invite under a **new `user_id`** (no
  agent call; old content stays hidden).
- **Do not** issue a removal while that contributor has a **live session** —
  Node owns session lifecycle.
- Node never writes `status` on the graph directly.

---

## 7. SP6b — Cross-contributor identity merges ✅

The agent already detects + merges duplicate entity cards (auto-merge / review /
unmerge). SP6b makes merges **provenance-correct** (survivor keeps the first
introducer's `told_by`) and **surfaces cross-contributor merges**. Node side:

- **Trigger the reconcile** as today: `POST /identity_merges/scan` for active
  legacies (e.g. at session-wrap or a low-frequency cron). If Node never calls
  it, auto-merge never fires (prevention still works).
- **Review pane / toast** — unchanged endpoints, **richer payload**:
  - `GET /identity_merges/suggestions?person_id=…`
  - `GET /identity_merges/auto_merged?person_id=…`
  Each item gains `cross_contributor: bool` + `source_told_by_display_name` /
  `target_told_by_display_name`, so the UI can render *"Priya's Amma and Ravi's
  Amma are the same person — merged."* When `cross_contributor` is false, keep
  the generic phrasing.
- **Act on items** (unchanged): `POST /identity_merges/suggestions/{id}/approve|reject`,
  `POST /identity_merges/{id}/acknowledge`, `POST /identity_merges/{id}/unmerge`.
- **Audience:** these are **per-legacy** feeds — Node decides which member(s)
  (usually the owner) see and act on them.

---

## 8. Endpoint catalogue Node calls (collaborator-relevant)

| Method | Path | Purpose | SP |
|---|---|---|---|
| POST | `/session/start` (+`/stream`) | send `user_id` + onboarding `session_metadata` | 1,3 |
| POST | `/turn` (+`/stream`) | send `user_id` | 1 |
| POST | `/profile_facts/upsert` | optional `user_id` | 1 |
| GET | `/event_links` | same-event link feed | 5 |
| POST | `/event_links/{id}/acknowledge` | dismiss link toast | 5 |
| POST | `/event_links/{id}/unlink` | reverse a link | 5 |
| GET | `/contradictions` | contradiction review queue | 5 |
| POST | `/contradictions/{id}/dismiss` | keep both | 5 |
| POST | `/collaborators/remove` | offboard a contributor | 6a |
| POST | `/collaborators/restore` | restore a contributor | 6a |
| POST | `/identity_merges/scan` | run entity reconcile | 6b |
| GET | `/identity_merges/suggestions` | pending merge reviews (+ cross-contributor fields) | 6b |
| GET | `/identity_merges/auto_merged` | auto-merge toast feed (+ cross-contributor fields) | 6b |
| POST | `/identity_merges/suggestions/{id}/approve\|reject` | act on a review | 6b |
| POST | `/identity_merges/{id}/acknowledge\|unmerge` | dismiss / reverse | 6b |

---

## 9. What Node must NOT do

- **Never write the canonical graph** (moments/entities/`status`/`told_by_*`,
  merge/link/contradiction tables). Reads + the agent endpoints only.
- **Never write the agent's `collaborator_onboarding`** table — mirror modal
  results via `session_metadata` instead.
- **Never touch DynamoDB `onboarding_complete` from the agent's view** — it
  stays Node's membership source of truth.
- **Don't infer the audience from the agent** — the agent emits per-legacy
  feeds; Node owns who sees them.

---

## 10. Suggested rollout order on the Node side

1. **Ship `user_id`** on all four request paths + drop `role_id` (unlocks SP1–4
   provenance/attribution/scoping at once).
2. **Onboarding modal** + `session_metadata` mirror (SP3).
3. **Review surfaces:** event-links + contradictions (SP5), then the
   identity-merge review pane/toasts (SP6b fields when they land).
4. **Removal** remove/restore + the restore-vs-fresh-start decision (SP6a).
