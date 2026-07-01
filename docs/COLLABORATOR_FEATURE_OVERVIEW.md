# The Collaborator Feature — Complete Implementation Overview

This is the plain-language compilation of everything built for **Collaborator
Phase 1 ("Open and Attributed")** — what each piece does, *why*, the
architecture decisions we made, and concrete examples. Read it to understand the
feature end to end without reading the code.

Running example throughout: the legacy subject is **David**. Contributors are
**Varun** (his son, the creator), **Keerthi** (his daughter), and **Ravi** (a
colleague). People David mentioned (e.g. **Amma**, **Rahul**) become *entities*.

---

## 1. What problem does this feature solve?

Flashback preserves a person's legacy by interviewing the people who knew them.
**Phase 1 lets *multiple* people contribute to one legacy** — not just the
creator. The moment more than one voice is involved, new questions appear:

- Who said this memory? (so we can say "your sister remembers it too")
- If two people describe the same event, do we keep both? What if they conflict?
- If a contributor's "Mom" and another's "Ishita" are the same person, can we
  merge them — without losing who introduced whom?
- If someone leaves (or asks to be removed), what happens to their stories?
- Which questions are OK to ask which contributor? (you don't ask a colleague
  the intimate things only the daughter would know)

"Open and Attributed" = **anyone invited can contribute** (open), and **every
contribution remembers who made it** (attributed). We built it as six sub-projects,
SP1 → SP6b.

---

## 2. The one idea everything rests on: `told_by_user_id`

Every contributor-authored row in the graph carries **`told_by_user_id`** — the
Node user id of the person who authored it.

- **`NULL` means "creator era"** — a row from before collaborators existed, or a
  system/seeded row. Because every old legacy had exactly one contributor, NULL
  safely means "the creator."
- **Only `moments.told_by_user_id` is load-bearing** (it drives attribution,
  hiding, removal). On entities/traits/questions/profile-facts it's
  *informational* — "who first introduced this."
- **The LLM never sees or sets it.** It's stamped in code at insert time
  (a recurring theme: **code over LLM** for anything structural).

Think of it as a signature on every sentence in the archive. Everything else —
attribution, scoping, linking, removal, merging — is built on reading that
signature.

---

## 3. The service boundary (who does what)

| Node.js backend owns | This Python agent owns |
|---|---|
| Auth, **users** (the identity behind `user_id`), membership | The canonical Postgres graph |
| Sessions, transcript (DynamoDB), the **UI** | All graph writes, Working Memory (Valkey) |
| All user-facing reads, showing the connection modal | Review/notification **feeds** |
| Deciding **who** sees a notification | Producing per-legacy notifications (audience-agnostic) |

The agent has **no auth** and **no concept of a target user**. It exposes
endpoints; Node calls them and decides who sees what. (Full Node to-do:
`docs/COLLABORATOR_NODE_INTEGRATION.md`.)

---

## 4. The six sub-projects

### SP1 — Provenance foundation *(migration 0026/0027)*

**Problem:** the graph didn't record who authored anything. A leftover `role_id`
field flowed around meaning nothing.

**What we built:** `told_by_user_id` columns on the contributor-authored tables;
Node sends `user_id` on every `/session/start` and `/turn`; the extraction worker
stamps it on every row it writes; `role_id` was retired (tolerated-and-ignored).

**Key decisions:**
- *Optional + no backfill.* `user_id` is optional so nothing breaks before Node
  ships its side; old rows stay `NULL` = creator era.
- *Display name denormalised only on moments.* Moments also store
  `told_by_display_name` ("Varun") because moments are the one place we render
  attribution; other tables keep just the id.
- *Supersession preserves the refining segment's author* (foundation rule D4#4).

**Example:** Varun tells "David taught me carrom." → a moment row with
`told_by_user_id = Varun`, `told_by_display_name = "Varun"`.

---

### SP2 — Speaker-first retrieval + attribution

**Problem:** with multiple voices, recall should lean toward *your own* memories,
and when it surfaces someone else's, it should *say whose*.

**What we built:** retrieval gives a gentle ranking nudge to the current
speaker's own moments; the response context tags any *other* contributor's moment
with `told_by="Name"`; the prompt tells the agent to credit them.

**Key decision — the attribution guard:** only credit when the moment's
`told_by_user_id` is non-null **and** differs from the current speaker **and** a
display name exists. Your own and creator-era content render plainly. This exact
guard is reused everywhere attribution appears (SP4-lite, SP5, SP6b).

**Example:** Varun asks about cricket; the agent pulls Keerthi's "last-ball six"
and replies *"Keerthi, his daughter, remembered the last-ball six that won the
cup."* If Varun had told it, it'd just be "the last-ball six."

---

### SP3 — Collaborator onboarding *(migrations 0028–0032)*

**Problem:** a brand-new collaborator needs a light "who are you to David / what's
one memory" warm-up — but it must never feel like a survey, and the agent must
not bluntly ask "what's your relationship?".

**What we built:** a per-`(person_id, user_id)` `collaborator_onboarding` row with
a sticky `phase ∈ {onboarding, active}`, graduating on **two** indirectly-captured
items:
1. **Connection** — satisfied by the Node connection modal (mirrored into
   `session_metadata` as `voice_anchor_text` etc.), or inferred from conversation.
2. **Memory** — satisfied when their first extracted moment commits.

An indirect "defining memory" **nudge** (a tap card) repeats each session until
they graduate. A guarded UPDATE flips `onboarding → active` once both are met.

**Key decisions:**
- *Relationship is modal-driven or inferred, never asked directly* (anti-survey).
- *Three separate "phase" concepts kept distinct:* the agent's
  `collaborator_onboarding.phase` (drives the nudge) ≠ the creator's
  `persons.phase` (cold-start) ≠ Node's DynamoDB `onboarding_complete`
  (membership). The agent never touches the last one.

**Example:** Ravi joins. Until he both has a relationship on file ("his
colleague") *and* his first moment is extracted, every session nudges him once
with a gentle "what's a memory of David that's stayed with you?" tap. Then he's
`active` and the nudge stops.

---

### SP4 — Question scoping (provenance + scope gated) *(migrations 0030/0031)*

**Problem (observed in real testing):** the creator got asked to continue a
moment the *daughter* had told; a friend got asked about intimate
father-daughter material. Questions leaked across contributors.

**What we built:** every produced question is LLM-labelled with a
`scope ∈ {public, personal, private}` (stored in `attributes`, code-normalised).
The question selector gates by scope **and** provenance:

| scope | who can be asked |
|---|---|
| `public` | everyone (work, hobbies, shared events) |
| `personal` | the teller + creator-era (`told_by IS NULL OR = you`) |
| `private` | the teller only (intimate: health, money, conflict) |

**Key decision — the LLM labels, the SQL enforces** (code over LLM). The model
picks the scope from the question's content; the database does the gating, so a
mislabel can't leak something the SQL forbids.

**Example:** Keerthi's session produces a `private` question about David's health
struggles, stamped `told_by = Keerthi`. When Ravi logs in, the selector never
offers it to him. A `public` "what work did David do?" can be asked of anyone.

*(Sub-piece — name recognition "lite":* when a contributor mentions an entity
another contributor introduced — Ravi says "Rahul" — the agent recognises it and
credits the source: "Rahul, the one Keerthi mentioned." Recognition only, no
merging.)*

---

### SP5 — Same-event linking + contradiction review *(migration 0033)*

**Problem:** two contributors inevitably describe the *same* event. Sometimes
complementary ("both at the wedding"), sometimes conflicting ("he was 60" /
"no, 65"). Before, complementary accounts sat disconnected and conflicts were
silently dropped to a log.

**What we built:** the extraction worker already compares each new moment against
similar existing ones via a small "compatibility" LLM. We added a **4th verdict**
and gave the old ones a home:

| verdict | meaning | action |
|---|---|---|
| `refinement` | same memory, newer is better | supersede the old *(existing)* |
| **`same_event`** *(new)* | same event, complementary | **auto-link both + notify** |
| `contradiction` | conflict, can't both be true | **record a review item** (was log-only) |
| `independent` | unrelated | nothing |

Two new tables (`moment_same_event_links`, `moment_contradictions`) mirror the
proven identity-merge lifecycle (auto + notify + reverse). Same-event links feed
`recall` retrieval into a `<linked_accounts>` block so the agent can say "your
brother remembers this too." **Contradictions never reach the agent** — they're a
Node-only review queue (the agent shouldn't fact-check mid-conversation).

**Key decisions:**
- *Resolve `told_by` live, never snapshot it (D5).* The link/contradiction rows
  store only moment ids; names are JOINed from `moments` at read time. Why:
  supersession can change a moment's active teller later, and a frozen snapshot
  would silently mislabel "who said it."
- *Supersession repoints these records (D6, extends invariant #5).* If a linked
  moment is later superseded, the link follows to the new id (re-canonicalising
  A/B order); a repoint that would self-pair collapses the row.
- *The cross-contributor refinement guard.* A `refinement` *erases* the older
  moment. We allow that only **within one voice**: if a *different* contributor's
  retelling is judged `refinement`, we **demote it to `same_event`** (link, keep
  both). So no contributor's account is silently erased by another's retelling.
  (The LLM still emits the verdict; the demotion is code.)

**Example — link:** Keerthi: "David's last-ball six won the Karimnagar final."
Ravi later: "I was at that final — his six won the cup." → `same_event` → linked.
Next time anyone recalls the final, the agent surfaces both, crediting Ravi.
**Example — contradiction:** Keerthi "his 60th"; Ravi "his 65th" → a pending
contradiction item Node shows for review; both moments stay; the agent says
nothing about the clash.

---

### SP6a — Collaborator removal (reversible hide) *(migration 0034)*

**Problem:** a contributor leaves, is removed by the owner, or asks for their
data to be taken down. What happens to their content — and to others' content
that depends on it?

**What we built:** `POST /collaborators/remove` / `restore`. Removal is a
**reversible hide** — a `status` flip to `'removed'`, never a delete — relying on
the existing `active_*` views so removed content vanishes from every read path
with **zero** retrieval/UI changes.

Removing contributor Y flips `status='removed'` on, in order:
1. Y's `collaborator_onboarding` row,
2. Y's **moments**,
3. **(resurrection)** any *surviving* contributor's moment that one of Y's
   now-removed moments had superseded — walking the supersession chain back to
   the nearest surviving voice (so Y's retelling doesn't collateral-hide
   someone else's account),
4. the **entities orphaned to Y** — ones Y introduced that *no surviving moment*
   references. Shared entities stay.

Everything else (edges, traits, questions, facts, threads, themes) is untouched.

**Key decisions:**
- *Reversible hide, not hard delete.* It's actually *simpler* than deleting
  (the views do the hiding; no cross-table FK scrub) and gives free re-invite.
- *Orphaned-entity rule (refines D4#1).* The original rule kept *all* entities;
  we hide the ones exclusively Y's — the GDPR-friendlier "remove what's only
  theirs," while keeping anything another contributor relies on.
- *Restore is the exact inverse — including a recursive re-supersede.* This was a
  bug the code review caught: removal's resurrection is recursive (can resurrect
  a moment buried 2+ hops behind the departing voice), so restore's re-supersede
  had to be recursive too, or a buried ancestor would wrongly stay active.
- *Re-invite, two ways:* **restore** the same `user_id` (content returns), or
  **fresh-start** a new `user_id` (old content stays hidden) — Node's choice, no
  agent work for the latter.

**Example:** Ravi is removed. His moments and the "OnlyRavi" entity he alone
mentioned disappear; the "Rahul" entity that Keerthi *also* mentioned stays. If
one of Ravi's moments had superseded a Keerthi moment, hers comes back. Restore
puts it all back exactly. A *surviving* shared entity Ravi introduced renders
un-attributed while he's removed (his name JOIN is gated to active onboarding),
and re-attributes on restore.

---

### SP6b — Cross-contributor identity merges *(migration 0035)*

**Problem:** the merge machinery already collapses duplicate entities (two
"Amma" cards) across contributors — but it was *provenance-blind* (the survivor
kept whichever row won, not the first introducer) and a cross-contributor merge
looked no different from a within-contributor one.

**What we built:** two refinements to the existing merge (detection unchanged):
1. **Survivor keeps the earliest introducer's `told_by`** — the older entity's
   (by `created_at`); ties keep the survivor's own; creator-era NULL is valid.
   Unmerge restores both the resurrected source's and the survivor's prior
   `told_by`.
2. **Surface it.** Both originals' `told_by` are captured on the merge record at
   creation; the review/toast feeds expose `cross_contributor` + both
   contributors' display names (resolved live from `collaborator_onboarding`).

**Key decisions:**
- *Earliest-introducer wins* — a merge collapses two identities, so the merged
  one is credited to whoever introduced it first (merge-specific; distinct from
  the reuse-fold "never restamp" rule).
- *Capture both `told_by` on the record, don't resolve purely live* — because
  decision #1 rewrites the survivor's `told_by`, after which the original pair is
  unrecoverable from the live rows. So we snapshot both at merge time.
- *No different-surface-form detection.* "Mom" = "Ishita" (no shared name) is
  **not** auto-merged — deliberately, to avoid false merges. Detection stays on
  name/alias evidence; embedding distance remains verifier context, never a
  trigger.

**Example:** Keerthi made an "Amma" card (Jan 1); Ravi made one (Mar 1). The
scan merges them; the survivor's `told_by` becomes **Keerthi** (earlier); the
toast says *"Keerthi's Amma and Ravi's Amma are the same person — merged."*
Unmerge splits them back, each with its own original author.

---

## 5. Architecture decisions that span the whole feature

These recurring choices are *why* the feature is robust:

1. **Code over LLM for anything structural.** The LLM labels/judges (scope,
   same_event/contradiction, merge verdicts); **code** does the gating, stamping,
   linking, merging, removal. A wrong LLM label can't cause a structural leak.
2. **Status flips, never deletes.** Supersession (`superseded`), merges
   (`merged`), removal (`removed`) are all `status` changes. Nothing is destroyed,
   so almost everything is reversible (unmerge, restore, dismiss, unlink).
3. **The `active_*` views are the single gate.** Every read filters
   `status='active'`. That's why hiding a contributor needs *no* retrieval/UI
   change — flip the status and it vanishes everywhere; flip it back and it
   returns.
4. **Resolve provenance live, snapshot only when you must.** SP5 resolves
   `told_by` live (supersession can change it). SP6b snapshots it on the merge
   record (the merge itself rewrites it). The rule: snapshot only when the live
   value will be overwritten.
5. **One attribution guard, reused everywhere.** "Credit only a *different*,
   identifiable contributor" appears in SP2 (moments), SP4-lite (entities), SP5
   (linked accounts), SP6b (merge names). Same logic, consistent behaviour.
6. **Reversibility + undo snapshots.** Merges and removals capture exactly what's
   needed to reverse them; restore/unmerge are *exact* inverses (the recursive
   re-supersede was the subtle case the review caught).
7. **Notifications are per-legacy and audience-agnostic.** The agent emits feeds
   keyed by `person_id`; **Node** decides which member(s) see them. Merges/links
   are graph-level corrections, not contributor-scoped like questions.

---

## 6. Reference

### Migrations
| # | What |
|---|---|
| 0026/0027 | `told_by_user_id` columns + expose on `active_*` views (SP1) |
| 0028–0032 | `collaborator_onboarding` table, display name, phase (SP3) |
| 0033 | `moment_same_event_links` + `moment_contradictions` (SP5) |
| 0034 | `'removed'` status on moments/entities (SP6a) |
| 0035 | `source/target_told_by_user_id` on `identity_merge_suggestions` (SP6b) |

### Invariants added to CLAUDE.md
- **#26** — contributor provenance is stamped, never inferred; never restamped on reuse.
- **#27** — question eligibility is provenance + scope gated (SP4).
- **#28** — same-event linking + contradiction review; live provenance; supersession repoint; the cross-contributor refinement guard (SP5).
- **#29** — reversible collaborator removal; orphaned-entity rule; recursive resurrection/re-supersede (SP6a). (SP6b extends #17.)

### Agent endpoints (collaborator-relevant)
`/session/start` & `/turn` (+stream) carry `user_id`; `/profile_facts/upsert`
takes optional `user_id`; `GET /event_links` + ack/unlink; `GET /contradictions`
+ dismiss; `POST /collaborators/remove|restore`; `POST /identity_merges/scan`,
`GET /identity_merges/suggestions|auto_merged` (now with `cross_contributor` +
names), approve/reject/acknowledge/unmerge.

### Modules
`flashback.moment_links` (SP5), `flashback.collaborators` (SP6a),
`flashback.identity_merges` (SP6b extends), plus provenance stamping in
`flashback.workers.extraction` and scope gating in `flashback.questions` /
`flashback.phase_gate`.

---

## 7. A note on the test baseline

While building SP5/SP6 we found ~24 pre-existing test failures on the branch —
all **stale tests** that hadn't kept up with deliberate product evolution
(profile-fact embedding, 1024-dim Voyage enforcement, subject-centered P2
questions, admin-token auth, the health SQS check, coverage-tap reading the DB,
a retired `role_id`, coverage counters climbing past 1). We root-caused each and
fixed the tests (one real code-defensiveness guard in `select_coverage_tap`).
The whole suite (~987 tests) is now green, so any future failure is genuinely new.

The SP6a/SP6b work was independently code-reviewed; the review caught one real
bug (the non-recursive restore re-supersede), which was fixed and locked in with
tests.
