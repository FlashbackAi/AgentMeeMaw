# LEGACY_MODE_BRIEF.md — Product context for the Node integration

This is the one-page product brief for **Legacy Mode**, a new
Flashback feature being added as a **new module ("v2") inside the
existing Node.js backend repo**.

> **The v1 / v2 split (same repo)**
>
> - **v1** = the existing Flashback features in this Node backend:
>   Flashback creation, the existing chat surface, and everything the
>   service does today. v1 code is **not** being modified by this
>   integration.
> - **v2** = the new Legacy Mode module you are building. It lives in
>   the **same repo** as v1, in a parallel folder/module, and reuses
>   the repo's existing infrastructure (HTTP framework, config,
>   secrets, Postgres client, SQS infra, auth, logger, tests,
>   deployment).
>
> v1 and v2 are sibling modules. The diff for this work touches only
> v2 files. Nothing in v1 is modified, refactored, renamed, or
> re-formatted.

The companion technical documents are `API.md` (HTTP contract) and
`NODE_INTEGRATION.md` (integration brief). Read this first for product
shape; read those for endpoint and column-level detail.

---

## 1. What Legacy Mode is

Legacy Mode preserves a person's legacy across **living subjects,
deceased subjects, and ancestors the contributor never met directly**.
A contributor — spouse, child, sibling, friend, descendant, colleague —
talks to an interviewer agent about one specific person. Over many
sessions, the system progressively builds a structured, evidence-linked
memory graph of that person's life: the moments, the people and places
involved, the character traits that emerge, the threads that connect
episodes.

This is legacy preservation across multiple subject contexts. A few
rules from that:

- The agent is an **interviewer / archivist**, not an impersonator. We
  do not build a "talk to Dad" chatbot. The agent asks; the contributor
  answers.
- We do not clone voices. We do not generate photoreal video of the
  subject.
- We do generate **Pixar-style stylized artifacts** — images for
  persons, threads, entities; short videos for moments — purely for
  visual texture in the legacy review UI.
- The conversation must never feel like a survey. Cold openers,
  dropped references, emotional pacing, all matter.
- We deliberately do **not** ask for date of birth or date of death up
  front. Lifespan is derived later from the time anchors of moments the
  contributor naturally shares.

---

## 2. How Legacy Mode differs from existing v1 features

The existing Flashback features (creation, chat, everything v1 does
today) are different products. Legacy Mode is a new module added
alongside them in the same repo:

| | v1 (existing chat / creation) | v2 (Legacy Mode) |
|---|---|---|
| Subject | the user themselves / general | one specific legacy subject per legacy |
| Goal | conversational utility | building a structured memory archive |
| Backend | existing v1 modules | new v2 modules in the same repo → Python agent service |
| State | v1's existing stores | working memory in Valkey + canonical graph in Postgres (agent-owned) |
| Output | chat replies | chat replies **plus** moments, entities, threads, traits, profile facts, and stylized artifacts that accumulate over time |

**v2 does not modify v1 code, does not call v1 product features at
runtime, and does not duplicate v1 features.** It's a parallel module
that happens to share the same repo, infrastructure, and process.

---

## 3. The user journey

1. **Legacy creation / onboarding — Node-owned UX with agent writes.**
   A contributor creates a "legacy" for one subject. Node collects the
   subject's name, the contributor's relationship to them, the
   subject's gender, the contributor's own display name, and any
   optional photo. Node calls the agent's `POST /persons`, shows the
   archetype questions returned by
   `GET /api/v1/onboarding/archetype-questions?person_id=...`, and
   completes the step with `POST /api/v1/onboarding/archetype-answers`.
   The agent stores onboarding completion and answers on `persons`
   because v1 has one contributor per legacy. The returned `session_id`
   is used for the first `/session/start`.

2. **Conversation.** The contributor opens the legacy and starts
   talking. Each conversation is a "session." Node calls the agent's
   `/session/start`, then `/turn` per message, then `/session/wrap`
   when the conversation ends (or on inactivity). The agent picks the
   opener and every reply. The contributor sees a chat-like surface.

3. **The legacy review UI.** Between conversations, the contributor
   can browse what they've built — a profile page for the subject,
   a moments timeline, a threads view, entities (people, places,
   objects, organizations that came up), a trait list, and a profile
   Q+A list ("profile facts"). Each of these renders rows from the
   agent's Postgres. Most of them have stylized images; moments have
   short stylized videos.

4. **Edits.** The contributor can correct or refine entries from the
   review UI — fix a moment's narrative, edit an entity's description,
   update a profile fact, or approve/reject identity-merge suggestions
   the agent surfaces ("Were 'Grandpa Joe' and 'Joseph' the same
   person?"). All edits go through agent endpoints; Node never writes
   the canonical graph directly.

---

## 4. Phases the UI should be aware of

A new legacy starts in **`phase = 'starter'`**. The agent uses this
phase to ask broad anchor questions across five dimensions (sensory
memories, the person's voice and mannerisms, places, relationships,
era / life period). Once the agent has at least one moment in each of
the five dimensions, the legacy automatically transitions to **`phase
= 'steady'`** and the question style shifts to deeper follow-ups on
existing threads.

The UI may want to surface phase as gentle progress feedback ("we're
still getting to know him" vs "filling in the picture"). Don't expose
the literal phase string. Don't expose the per-dimension counters.
Phase transitions are sticky — the UI never sets phase; only the
agent does.

---

## 5. Asynchrony the UI must accommodate

These are summarised here from `NODE_INTEGRATION.md` §7 because they
shape the UI:

- **Stylized images and videos appear with delay.** When a moment is
  created or an entity is added, its `video_url` / `image_url` will
  initially be null. A separate worker (built as part of this
  integration) generates and uploads the artifact, then writes the
  URL. The UI must render a graceful placeholder.
- **The latest session's content lands ~30s after wrap.** The legacy
  review UI may not show the just-completed conversation's moments
  immediately. Re-query on next page load; don't poll.
- **Edits supersede.** Editing a moment creates a new row and marks
  the old one superseded; the UI's stable identifier should switch to
  the new row's id. Editing an entity is in-place; the id is stable.

---

## 6. What v2 ships

v2 is a new module in the existing repo, scoped to Legacy Mode only.
Deliverables (full checklist in `NODE_INTEGRATION.md` §12):

- **Onboarding UX + `persons` row creation.** Node-only flow: name,
  relationship, optional photo. Resolves the §3 open question for the
  `persons` write mechanism before shipping.
- **A typed agent HTTP client** wrapping `/session/start`, `/turn`,
  `/session/wrap`, `/nodes/{type}/{id}/edit`, `/profile_facts/upsert`,
  `/identity_merges/*`, `/admin/reset_phase` (and `POST /persons` if
  option (a) is chosen for onboarding).
- **Routes for the Legacy Mode conversation** —
  open / message / close — sitting inside the repo's existing auth
  middleware.
- **Routes for the legacy review UI's writes** — moment / entity
  edits, profile fact upserts, identity merge approve/reject — all
  proxied to agent endpoints.
- **Routes that read the agent's Postgres directly** (read-only,
  scoped by `status='active'` and `person_id`) for the legacy review
  UI's lists and detail pages.
- **An SQS consumer for `artifact_generation`** — receives one
  message per artifact-bearing row, calls the repo's existing
  image/video generation pipeline, uploads to S3, writes the URL
  columns back to Postgres.
- **Per-turn transcript logging to DynamoDB.**

v2 reuses the repo's existing infrastructure for all of the above —
HTTP framework, config, secrets, Postgres client, SQS pattern, auth
middleware, logger, test framework, deployment. v2 does not introduce
parallel infrastructure.

---

## 7. Out of scope (for this integration)

- Any modification to v1 code. Existing chat, Flashback creation, and
  everything v1 does today must be byte-identical after this work.
  No edits, no refactors, no renames, no formatter sweeps, no drive-
  by improvements.
- Voice synthesis, photoreal video, "talk to the subject" interaction
  modes — these are explicitly not part of the product.
- Pushing onto the agent's `extraction` or `embedding` SQS queues —
  the agent does that itself.
- Writing to canonical graph columns other than the eight URL columns
  enumerated in `NODE_INTEGRATION.md` §6.5.
- Admin/debug UI for phase reset — the agent endpoint exists
  (`/admin/reset_phase`) but doesn't need a UI surface unless
  separately requested.
