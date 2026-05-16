# CLAUDE.md — Flashback AI: Legacy Mode (Agent Service)

This file is the operating manual for any contributor — Claude Code or
human — working in **this repo**. Read it before touching code.

> **Repo scope:** This repo is the **Python agent service only**. The
> Node.js Backend lives in a **separate repo**. We do not edit Node code
> here. We define the contract with Node and stay on our side of it.

---

## 1. Product context

Legacy Mode preserves a person's legacy across living, deceased, or
never-met subjects. A contributor (spouse, child, sibling, friend,
descendant, colleague, mentor/mentee, etc.) talks with the agent about a
specific person, and the system progressively builds a structured,
evidence-linked memory graph of that person's life and stories.

This is **legacy preservation** across multiple subject contexts. A few
rules flow from that:

- We do **not** build a "talk to Dad" chatbot. The agent is an
  interviewer/archivist, not an impersonator.
- We do **not** clone voices or generate photoreal video of the subject
  in v1.
- We **do** generate Pixar-style stylized artifacts (images for persons /
  threads / entities, short videos for moments) for visual texture.
- The agent must never feel like a survey. Cold openers, dropped
  references, and emotional pacing all matter.
- DOB / DOD are deliberately **not** stored on `persons`. Asking up
  front is cold. Lifespan is derived from moment time anchors.

---

## 2. Tech stack (this repo)

| Concern | Choice |
|---|---|
| Language / runtime | Python |
| LLMs (small calls) | OpenAI — `gpt-5.1` |
| LLMs (big calls) | Anthropic — `claude-sonnet-4-6` |
| Embeddings | Voyage AI (`voyage-3` or `voyage-3-large`, 1024-dim) |
| Canonical graph | Postgres + pgvector |
| Working memory | Valkey (Redis-compatible) |
| Queues | AWS SQS — `extraction`, `embedding`, `artifact_generation` |

External dependencies we **call** but do not own: the Node Backend
(REST), Anthropic API, OpenAI API, Voyage API, Postgres, Valkey, SQS.

---

## 3. Service boundaries — what's ours, what isn't

```
┌──────────┐     ┌──────────────────┐     ┌─────────────────────┐
│ Frontend │ ──▶ │ Node.js Backend  │ ──▶ │  THIS REPO          │
└──────────┘     │  (separate repo) │     │  Python Agent Svc   │
                 └────┬─────────────┘     └────┬────────────────┘
                      │                        │
                ┌─────┼─────┐         ┌────────┼─────────────────┐
                ▼     ▼     ▼         ▼        ▼                 ▼
            DynamoDB  S3  Postgres  Postgres  Valkey      SQS (3 queues)
            (Node)  (Node) (reads,  (writes,  (working    extraction
                          Node)     ours)     memory,     embedding
                                              ours)       artifact_gen
```

### What this repo owns

- The **Turn loop**: Conversation Gateway, Turn Orchestrator, Phase
  Gate, Intent Classifier, Retrieval Service, Response Generator.
- The **Segment loop**: Segment Detector, Extraction Worker.
- The **Background loop**: Thread Detector, Trait Synthesizer, Profile
  Summary Generator, Question Producers P2/P3/P5. (P1 inline in
  Extraction Worker. P4 inline at end of Thread Detector. P0 is a
  one-time seeder migration.)
- **All writes** to Postgres canonical graph (persons, moments,
  entities, threads, traits, questions, edges, history tables).
- **Working Memory** in Valkey (per-session ephemeral state).
- **Pushing jobs** onto all three SQS queues (extraction, embedding,
  artifact_generation).
- The **embedding worker** that drains the embedding queue and writes
  vector columns back to Postgres.

### What Node.js (other repo) owns

- Auth and users. Multi-contributor `person_roles` are deferred in v1.
- Sessions and per-turn transcript log → DynamoDB.
- **All user-facing reads** from Postgres for the UI (legacy review
  surfaces).
- **Consuming** the `artifact_generation` queue: calling the
  image/video generation model, uploading to S3, writing the URL
  columns (`image_url`, `video_url`, `thumbnail_url`) back to Postgres.

### Hard rules

- **We never touch DynamoDB.** Session/turn metadata we need is passed
  in by Node on the request, or fetched from a Node API.
- **We never touch S3 or the URL columns.** We only write the
  `generation_prompt` column and push onto `artifact_generation`.
- **We never write to Node-owned tables** (users, future
  `person_roles`, etc.). In v1 onboarding state is stored on the
  agent-owned `persons` row because there is only one contributor per
  legacy.
- **No auth in this service.** Trust comes from a service-to-service
  token plus private network. Node is the auth boundary.
- **Node never writes to the canonical graph.** Reads only. If Node
  needs a write surface, we expose an agent endpoint for it.

---

## 4. The 18 invariants

Every piece of code touching the graph or queues must respect these.

1. **Filter `status='active'`** in every query against canonical
   tables. Prefer the `active_moments` (and equivalent) views.
2. **Always filter `person_id`** in similarity / vector searches.
   Never let a query cross legacies.
3. **Never mix vectors from different embedding models.** Every
   embedded row stores `embedding_model` + `embedding_model_version`.
   Re-embed on model change.
4. **Never generate embeddings inline.** Writes that need an embedding
   push a job to the `embedding` queue. The embedding worker fills it
   in.
5. **Supersession repoints ALL edges in the same transaction.** When a
   moment is superseded, every edge pointing at it is repointed to the
   new canonical row atomically.
6. **The Extraction Worker under-extracts.** No staging store in v1.
   Low-confidence material is dropped, not flagged.
7. **Working Memory in Valkey is ephemeral.** Anything that must
   persist is logged by Node into DynamoDB.
8. **No auth in this service.** Trust the network + token. See §3.
9. **Producers must populate `attributes.themes`** on every question
   they emit. Diversity and adjacency ranking depend on it.
10. **Cap universal-dimension questions at 1 per top-5** when ranking
    the next-question slate. Otherwise it feels like a survey.
11. **The Segment Detector is an LLM call**, not rules. It runs on a
    fixed user-turn cadence: once every
    `segment_detector_user_turn_cadence` user turns (default 6; 1 turn
    = 1 user message + 1 assistant reply). The orchestrator increments
    `signal_user_turns_since_segment_check` in Working Memory on each
    user-turn append, and resets it to 0 after every detector
    invocation regardless of whether a boundary fires. Single source of
    truth is `segment_detector_user_turn_cadence` /
    `SEGMENT_DETECTOR_USER_TURN_CADENCE`.
12. **Session Wrap pushes the Segment Detector with `force=true`.**
    That is the single mechanism that flushes the tail of a session
    into the extraction queue.
13. **Post-session sequencing is fixed.** Session Wrap → Extraction
    Worker drains → Trait Synthesizer → Profile Summary → P2/P3/P5
    in parallel. The Thread Detector is **not** part of this chain —
    it runs on its own count-based cadence (see #14). P4 runs inline
    at the end of the Thread Detector when it does run.
14. **Thread Detector runs every 15 new active moments.** Gate: total
    active moments ≥ 15. Trigger:
    `count(active_moments) - moments_at_last_thread_run ≥ 15`. After
    it completes, update `moments_at_last_thread_run` on the person.
15. **The rolling summary is owned by the Segment Detector path and
    is strictly within-session.** Born empty at `/session/start` —
    never seeded from a prior session. On segment boundary,
    regenerate the rolling summary (small LLM) over
    `(prior_rolling_summary + closed_segment_turns)`, store it in
    Working Memory, and include it in the `extraction` queue payload.
    The Extraction Worker reads it as compressed *in-session* context
    when generating moments. The rolling summary is always a fresh
    compressed rewrite, not an append — never let it grow unbounded.
    Cross-session recall lives in a separate Working Memory field,
    `prior_session_summary` (seeded once at session start, read-only,
    consumed only by the Response Generator). Keeping these two
    fields distinct is what prevents extraction from re-mining
    already-extracted moments when a new session is short.
16. **Profile facts are open-ended but capped.** `profile_facts` rows
    are `(question, answer)` pairs surfaced on the legacy profile.
    `fact_key` is a free-form snake_case slug picked by the
    extraction LLM (or by Node on user edit), not a fixed registry.
    Hard cap: **25 active facts per person** — at the cap, only
    updates to existing keys are accepted, new keys are rejected. Per
    profile-summary run, at most **5** new/updated facts are written
    (LLM tool schema + code-side enforcement). Edits supersede via
    status flip + new row, mirroring moments / entities / threads.
    Every new active row pushes an `embedding` queue job.
    `POST /profile_facts/upsert` is the only Node-driven write path.
17. **Identity merges require user approval.** Extraction may create a
    pending `identity_merge_suggestions` row when an alias/correction
    indicates two active entities may be the same. It must not directly
    merge entities. Approval happens through
    `POST /identity_merges/suggestions/{id}/approve`, ideally surfaced
    by Node/UI as an out-of-band toast or review item, not inside the
    memorial conversation. Approval repoints edges, marks the source
    entity `merged`, clears survivor embedding fields, and queues a
    fresh entity embedding job.
18. **Traits are anchored, deduped, and behavior-described.** Three
    rules, applied in order by the Extraction Worker:

    a. **Exemplifier required.** A trait is a stable pattern, not a bare
       adjective and not a single incident. The extraction prompt
       instructs the LLM to skip candidates without behavioral anchoring
       and to connect each surviving trait via
       `exemplifies_trait_indexes` on a moment. As a backstop,
       `drop_orphan_traits` runs on the LLM output before persistence:
       any trait not referenced by a moment's
       `exemplifies_trait_indexes` is dropped, and surviving traits'
       indexes are remapped so moment edges still resolve.

    b. **Cross-session merge.** Before persistence, the worker matches
       each surviving trait against `active_traits` by
       case-insensitive `name` (per `person_id`). On match,
       `merge_trait_description` (small LLM) blends the existing and
       new descriptions into a 1-2 sentence behavior-focused
       description; persistence then UPDATEs the existing row
       (description + NULL embedding fields) and routes
       `exemplifies` edges to the existing trait id — never a
       duplicate insert. An `embedding` job is pushed for the
       merged description so the embedding worker re-embeds.

    c. **Descriptions are about the subject, not the speaker.** Trait
       descriptions live on the subject's legacy and must describe
       the SUBJECT's observed property in behavioral terms. The
       contributor is excluded entirely from trait descriptions. The
       general contributor-name attribution rule (used elsewhere in
       extraction) does NOT apply inside trait descriptions: both
       "Described as kind by the contributor" and "Described as kind
       by Priya" are forbidden. A good description names the property
       and a concrete behavior that shows it ("Came across as kind
       from the first meeting — made time for a stranger's laptop
       questions without seeming bothered").

19. **Retrieval is intent-gated.** Semantic vector retrieval fires only
    on intents that benefit from it. The matrix is implemented in
    `orchestrator/steps/retrieve.py` and mirrored in the Intent
    Classifier prompt's OUTCOMES section so the classifier reasons
    over response shape, not just input signal:

    | intent  | search_moments | search_entities | get_entities | get_threads | Voyage |
    |---------|----------------|-----------------|--------------|-------------|--------|
    | recall  | yes            | yes             | —            | —           | 2      |
    | switch  | —              | —               | yes          | yes         | 0      |
    | clarify | —              | —               | —            | —           | 0      |
    | deepen  | —              | —               | —            | —           | 0      |
    | story   | —              | —               | —            | —           | 0      |

    The `query` argument to vector searches is `state.user_message`
    (the literal text the user typed) — only meaningful on `recall`,
    which is why the matrix gates it there.

20. **Entity-mention scanning is deterministic and intent-independent.**
    Sits between `classify` and `retrieve` in the orchestrator pipeline.
    Every user turn scans `user_message` against a Valkey-cached,
    per-person catalog of active entity names + aliases. Word-boundary,
    case-insensitive match; longest-name-first to avoid partial-form
    collisions. Object-kind entities are excluded (false-positive
    risk on common nouns). Hits are loaded by id via
    `get_entities_by_ids` and rendered into `<mentioned_entities>`
    in the response generator context; when one surface form resolves
    to two or more distinct active entities the block is rendered
    with `ambiguous="true"`. Cache key `entity_names:{person_id}` is
    cache-aside (reload from Postgres on miss) and DEL'd by the
    Extraction Worker after entity writes commit. This surface is
    free (no Voyage call) and orthogonal to invariant #19.

21. **Starter-question dedup uses the WM `asked` register, not just
    `answered_by` edges.** The graph-anchored dedup
    (`SELECT_UNANSWERED_*` filters by NOT EXISTS over `answered_by`
    edges to moments) only works once a moment has been extracted —
    which leaves shallow-content sessions stuck on the same starter
    template. The fix is the per-session Valkey LIST written by
    `select_question` and the session-start route, consumed by both
    starter and steady selectors (`recent_ids` parameter). Three-step
    fallback: unanswered + not-recent, then drop unanswered, then drop
    recent — better to repeat than to crash when the bank exhausts.

22. **Themes are the user-facing layer; anchors stay internal.** The
    `themes` table holds user-visible groupings of moments — five
    `universal` themes (`family`, `career`, `friendships`, `beliefs`,
    `milestones`) seeded into every legacy at person creation, plus
    `emergent` themes auto-promoted by the Thread Detector. Anchor
    dimensions (sensory / voice / place / relation / era) remain
    internal cold-start coverage signals and never surface in the UI.
    Five hard rules govern themes:

    a. **Tagging is multi-tag and LLM-emitted.** The Extraction
       Worker passes the active theme catalog (universals + active
       emergents for this subject) into its user message, and the
       LLM populates `moment.themes: [<slug>]` per moment. A wedding
       moment carries both `family` and `milestones`; a Sunday
       church story carries `family` and `beliefs`. Persistence
       writes one `themed_as` edge per resolvable slug; unknown
       slugs are dropped silently (invariant #6).

    b. **Emergent themes are 1:1 with new threads.** When the
       Thread Detector names a new thread, the same naming LLM
       call decides whether the cluster is also a discrete passion
       / practice / place that universals don't already cover. If
       yes, the worker eagerly generates archetype questions
       outside the transaction and inserts an emergent theme row
       linked to the thread, then back-tags the cluster moments
       via `themed_as`. Threads remain internal scaffolding;
       emergents are the user-facing wrapper.

    c. **Locked themes are always visible; tier is computed.** All
       themes start `state='locked'` and the `active_themes_with_tier`
       view derives tier (`tale` / `story` / `testament`) on read
       from the live `themed_as` × `active_moments` join. Locked
       themes report `tier=NULL`. There's no denormalised counter
       to drift. "Qualifying" moment = has any of (`sensory_details`,
       `time_anchor`, an `involves` edge); "rich sensory" =
       `len(sensory_details) > 80`. Tunable via the view definition.

    d. **Unlock = lazy archetype gen + atomic flip on session start.**
       `POST /themes/{id}/unlock_prepare` returns archetype MC
       questions, generating + caching them via a small LLM on first
       call. The theme stays locked. The actual lock→unlocked flip
       happens atomically inside `apply_theme_unlock` (orchestrator
       step) on the next `/session/start` when `session_metadata`
       carries `theme_id` (+ optional `archetype_answers`). Answers
       are ephemeral priors: persisted as JSONB on the theme row,
       injected into the first-turn opener context, but **never**
       written as moments/traits/profile_facts. Extraction mines
       the resulting conversation, not the answers.

    e. **Deepen flow is soft bias, never hard filter.** When
       `current_theme_slug` is set on Working Memory, the producer
       ranker adds `THEME_BIAS_WEIGHT * 1.0` to candidates whose
       `attributes.themes` overlap — large enough to break ties in
       favor of theme-aligned questions, small enough that a
       high-priority `dropped_reference` on a different theme still
       wins. Retrieval and the response generator surface the
       theme but follow the user when conversation drifts.

---

## 5. Schema invariants

- **Hybrid model:** strongly-typed node tables (`persons`, `moments`,
  `entities`, `threads`, `traits`, `questions`) **+ one generic
  `edges` table** that replaces all link tables and `evidence_*_ids`
  arrays.
- **Edge types:** `involves`, `happened_at`, `exemplifies`,
  `evidences`, `related_to`, `motivated_by`, `targets`, `answered_by`,
  `themed_as`. The `theme` kind is added to the edge from_/to_kind
  enums alongside the existing six.
- **`validate_edge()` in app code**, not DB constraints. Every write
  goes through it.
- **Supersession via `status`** (`active` | `superseded` | `merged`),
  not deletion.
- **Embeddings:** `vector(1024)` columns with `embedding_model` and
  `embedding_model_version` alongside, on every embedded row.
- **Subject of a legacy** lives in `persons`. Never duplicate the
  subject into `entities`. Other people mentioned in moments are
  entities of sub-type `person`.
- **Entities** have 4 sub-types: `person`, `place`, `object`,
  `organization`, with type-specific `attributes` JSONB and `aliases`.
- **Identity merge suggestions** live in `identity_merge_suggestions`.
  They are pending review records, not graph mutations. Extraction can
  propose source→target; only approval performs the merge.
- **Questions are first-class nodes.** Their relational data lives in
  `edges` via `motivated_by`, `targets`, `answered_by`. The questions
  `attributes` JSONB only holds non-relational fields:
  `dropped_phrase`, `life_period`, `dimension`, `themes`,
  `targets_fact_keys` (slugs in `profile_facts` an answer can fill).
- **Profile facts** live in their own table `profile_facts`:
  `(person_id, fact_key, question_text, answer_text, status, source,
  answer_embedding, ...)`. `fact_key` is a free-form snake_case slug
  with a unique partial index per active row per person. Edits
  supersede; never destructive UPDATE. Cap = 25 active per person.
  The seven seed slugs (`profession`, `birthplace`, `residence`,
  `faith`, `family_role`, `era`, `personality_essence`) are display
  defaults, not a hard registry.
- **Themes** live in their own table `themes`:
  `(person_id, kind, slug, display_name, description, state,
  archetype_questions, archetype_answers, thread_id, unlocked_at,
  image_url, generation_prompt, status, ...)`. `kind` is
  `'universal' | 'emergent'`, `state` is `'locked' | 'unlocked'`.
  Partial unique index on `(person_id, slug) WHERE status='active'`
  makes seeding idempotent. Emergent rows reference the originating
  `threads.id`; universals do not. The `active_themes_with_tier`
  view denormalises tier + counters from `themed_as` edges over
  `active_moments` as the read surface Node consumes (Node reads
  it directly per the integration contract; the agent exposes it
  via `POST /themes/{id}/unlock_prepare` for the unlock flow).

### Persons cold-start columns

- `phase TEXT NOT NULL DEFAULT 'starter'` — `'starter' | 'steady'`.
  Sticky.
- `coverage_state JSONB NOT NULL DEFAULT '{"sensory":0,"voice":0,"place":0,"relation":0,"era":0}'`
- `phase_locked_at TIMESTAMP NULL`
- `moments_at_last_thread_run INT NOT NULL DEFAULT 0` — used by the
  Thread Detector trigger (every 15 new active moments).

### Artifact columns (Node writes URLs, we write prompts)

- `image_url`, `video_url`, `thumbnail_url` (videos only on `moments`,
  images on `persons`/`threads`/`entities`).
- `generation_prompt TEXT` on each artifact-bearing table.

---

## 6. Cold-start machinery

A fresh legacy starts in `phase='starter'`. Goal: at least one moment
in each of the **5 anchor dimensions** — `sensory`, `voice`, `place`,
`relation`, `era`.

- **Phase Gate** (code) fires only on switch-intent `/turn` selection.
  Session openers no longer select an inline starter question.
- **Coverage taps** are global template questions seeded by migration
  (`source='coverage_tap'`, `attributes.dimension`, `attributes.themes`).
  They surface as **archetype-style tap cards** under the agent's reply,
  not inline questions. Each card has: the question text, 4 short
  tappable answer chips, a free-text input, and a Skip button.
- **`select_coverage_tap` step** runs on every `/turn` and emits one
  tap for the lowest zero-coverage dimension when intent is
  `switch`/`clarify`. Tiebreaker: `era > relation > place > voice >
  sensory` (cold to warm — sensory is asked last once we've earned the
  intimacy).
- **`promote_seeded_to_tap` step** runs on switch turns in starter
  phase only. When no coverage gap is open but the steady selector
  picked a question from the producer bank (P2-P5), that seeded
  question is promoted into a tap card too — so the archetype-style
  surface continues mid-chat through starter phase. In steady phase
  this step is a no-op; the bot inlines its question as normal.
- **Tap option chips are LLM-generated per turn** (small gpt-5.1 call
  in `flashback.orchestrator.tap_options`). Given the question, subject
  name + relationship, and gap dimension, the call returns 4 short
  concrete option strings (e.g. "Her quick smile", "Always in the
  kitchen"). Generation is best-effort: on failure the card falls
  back to question + free-text only. Options are NOT stored on the
  question row; they are regenerated each time the tap fires.
- **`tap_pending` response-generator branch.** When `state.taps` is
  set, the SWITCH / CLARIFY prompts switch to acknowledgment-only
  mode (one short sentence, no question, no options). The tap card
  IS the next question; the bot does not also speak one.
- **Cap and cooldown.** Maximum **2 taps per session**
  (`taps_emitted_this_session`). Additionally, a **2-user-turn
  cooldown** between taps (`user_turns_since_last_tap` in Working
  Memory) — async extraction means `coverage_state` lags real-time,
  so back-to-back taps would surface the same gap dim twice.
- **Tap-acceptance signal to Intent Classifier.** When a tap is
  emitted, `signal_pending_tap_question` is set in Working Memory.
  The classifier reads it on the next turn so a terse option-style
  reply ("Her quick smile") is classified as `story` / `deepen`,
  not `switch`. Signal is cleared after one classification.
- **First-turn opener** is LLM-generated under tight constraints:
  must (a) name the subject, (b) use onboarding details when present
  without re-asking them, and (c) open conversationally. Archetype
  answers and continuity context carry the cold-start load. Not
  templated.
- **Coverage Tracker** (code, runs after Extraction Worker) increments
  `persons.coverage_state` per moment based on extracted content.
- **Handover Check** flips `persons.phase` to `'steady'` and stamps
  `phase_locked_at` once all 5 dimensions are ≥ 1. **Sticky** — no
  auto-revert. Admin can manually reset.

### Coverage Tracker rules (per moment)

| Dim | Increments when |
|---|---|
| `sensory` | `sensory_details` non-empty |
| `voice` | trait extracted, OR linked entity has `saying`/`mannerism` attr |
| `place` | any `involves` edge to a `place` entity |
| `relation` | any `involves` edge to a `person` entity ≠ subject |
| `era` | `time_anchor` has a year, OR `life_period_estimate` is set |

Counters can climb past 1 — that's diagnostic. Only `≥ 1` matters.

---

## 7. Themes layer (user-facing)

Themes are the visible groupings of moments on the legacy/profile
screen. Five universals are seeded at person creation; emergents
auto-promote off the Thread Detector. See invariant #22 for the
hard rules.

### 7.1 Universal seeding

`persons.repository.insert_person` writes the persons row and the
five universal `themes` rows inside the same transaction (idempotent
via the partial unique index). Local dev's `/create-person` does the
same, and `/memories` self-heals by lazily seeding when it observes
zero active themes for a person — for legacies created on older
schemas or paths that bypass `insert_person`.

The five universals and their slugs are pinned in
`flashback.themes.universal`. Slug stability matters: it's part of
the unique index and gets referenced from the extraction prompt's
theme catalog. Display names can be tweaked freely.

### 7.2 Tagging

The Extraction Worker fetches the active theme catalog (universals
+ active emergents for this subject) just before its LLM call and
renders it as `<theme_catalog>` in the user message. The LLM emits
`themes: [<slug>]` per moment as part of the existing
`extract_segment` tool output (schema invariant: optional, default
empty). Persistence resolves slugs through the catalog map and
writes one `themed_as` edge per resolvable slug. Unknown slugs are
dropped silently (invariant #6).

The supersession path (moment → superseded) drops outbound edges
from the old moment as part of its existing tx, so refined moments
naturally re-acquire their tags from the fresh LLM emission.

### 7.3 Emergent promotion

The Thread Detector's naming LLM tool gains three optional fields —
`theme_display_name` (2–4 word phrase), `theme_slug` (snake_case),
`theme_description` (one sentence) — only set when the cluster is a
discrete passion / practice / place that universals don't already
cover. When set, the worker:

1. Eagerly generates archetype questions outside the transaction
   (small LLM call; falls back to empty list on failure).
2. Inside the existing per-cluster transaction, inserts the
   emergent theme row linked to the new thread, caches
   archetype_questions on the row, and writes `themed_as` edges
   from every cluster member to the new theme.
3. On the existing-match path (cluster latched onto a prior
   thread), looks up that thread's emergent theme (if any) and
   back-tags the new cluster's moments to it.

Threads stay internal scaffolding; emergent themes are the visible
wrapper.

### 7.4 Unlock + deepen flows

Unlock:

1. UI taps a `locked` card → `POST /themes/{id}/unlock_prepare`.
2. Agent returns archetype MC questions, generating + caching on
   the row when `archetype_questions IS NULL` (universals). On
   subsequent taps the cached payload is returned at no LLM cost.
3. UI shows 3–4 MC questions × 4 chips each with skip + free-text.
4. UI calls `POST /session/start` with `session_metadata.theme_id`
   + `session_metadata.archetype_answers`.
5. Orchestrator's `apply_theme_unlock` step flips the theme to
   `unlocked`, persists answers as JSONB, and propagates
   `current_theme_*` into Working Memory.
6. The opener carries the theme context. Conversation proceeds via
   the normal Turn Orchestrator (`/turn`).

Deepen (already-unlocked theme):

1. UI taps an `unlocked` card → straight to `/session/start` with
   `session_metadata.theme_id` (no archetype answers).
2. `apply_theme_unlock` is a no-op on the state flip but still
   stamps `current_theme_*` on Working Memory.
3. The producer ranker's soft bias kicks in — candidates whose
   `attributes.themes` overlap the active slug get
   `THEME_BIAS_WEIGHT * 1.0` added to their `combined_score`.
4. Retrieval and the response generator surface the theme but
   never hard-filter; the user can drift naturally and the agent
   follows.

### 7.5 Tier read surface

`active_themes_with_tier` is the canonical read surface. Node reads
it directly from Postgres (per the integration contract); the local
dev's `/memories` endpoint mirrors the same query. Tier rules:

| condition                                                  | tier      |
|-----------------------------------------------------------|-----------|
| `state='locked'`                                          | NULL      |
| qualifying ≥ 5 AND life_periods ≥ 3 AND has_rich_sensory  | testament |
| qualifying ≥ 3 OR life_periods ≥ 2                         | story     |
| qualifying ≥ 1                                            | tale      |
| else                                                      | NULL      |

"Qualifying" = moment has any of (`sensory_details`, `time_anchor`,
an `involves` edge). "Rich sensory" = `char_length(sensory_details)
> 80`. Tunable via the view; no denormalised counters drift.

### 7.6 Local-dev integration

`local/server.py`'s `/memories` endpoint includes a `themes` array
keyed off `active_themes_with_tier`, and self-heals by seeding
universals when it observes zero active themes. `local/static/
index.html` adds a fourth column to the memory panel rendering
cards with locked/tier badges and the unlock modal that calls
`/api/themes/{id}/unlock_prepare` then `/api/session/start` with
theme metadata.

---

## 8. Build order (this repo)

We build in this order. Each step gets its own Claude Code prompt; we
write them together as we go.

1. **Schema migrations** — node tables, generic `edges` table, history
   tables, phase/coverage columns, artifact URL/prompt columns,
   embedding-model columns, `active_*` views.
2. **Coverage tap seed migration** (formerly Producer 0 output).
3. **Embedding worker** — drains `embedding` queue, calls Voyage,
   writes vector + model + version. The whole pipeline (what gets
   stored, when triggers fire) is documented in `ARCHITECTURE.md` §6.
4. **Conversation Gateway + Working Memory** — Valkey schema,
   hydration, write-back.
5. **Intent Classifier** (small LLM) — outputs `intent`, `confidence`,
   `emotional_temperature`.
6. **Retrieval Service** — tool surface over the canonical graph,
   gated by intent per invariant #19. The deterministic entity-mention
   scanner (invariant #20) is a separate surface in
   `flashback.entity_mention` plus `orchestrator/steps/entity_mention_scan`.
7. **Response Generator + session opener** — big LLM, prompt families
   per intent.
8. **Phase Gate + question/tap selection** — code; steady question
   selection plus structured coverage taps. Tap card surface is built
   from three steps: `select_coverage_tap` (gap-driven),
   `promote_seeded_to_tap` (starter-phase only — promotes a steady
   seeded question into a tap), and a small gpt-5.1 call in
   `flashback.orchestrator.tap_options` that generates 4 option chips
   per emitted tap.
9. **Turn Orchestrator** — the loop: append turn → intent → tap
   selection / retrieval → seeded-question selection → promote-to-tap →
   response → append response → segment detector.
10. **Segment Detector** (small LLM) — runs every turn after buffer
    threshold; emits boundary or "not yet".
11. **Extraction Worker** (big LLM) — moments + entities + traits +
    edges + inline P1 dropped-references; refinement detection and
    supersession; pushes embedding + artifact jobs.
12. **Coverage Tracker + Handover Check** — code; runs after
    extraction.
13. **Artifact queue push** — agent-side enqueue. Node consumes
    elsewhere.
14. **Thread Detector** — count-based trigger (every 15 new active
    moments, gated by total ≥ 15); clustering + match-or-create +
    inline P4 `thread_deepen` questions.
15. **Trait Synthesizer** (small LLM) — strength ladder
    (`mentioned_once → moderate → strong → defining`).
16. **Profile Summary Generator** — display name, top 5–7 traits, key
    threads, life period, key entities. Also runs profile-fact
    extraction as a second LLM call and writes to `profile_facts`
    (≤5 high-confidence Q+A pairs per run, capped at 25 active per
    person).
17. **Question Producers P2 / P3 / P5** — per-session and weekly.
18. **Session Wrap** — force-close segment, session summary, invoke
    profile summary, fan out to background workers.
19. **Profile Facts edit endpoint** — `POST /profile_facts/upsert`
    (Node-driven). Supersedes the prior active row, writes a new one
    with `source='user_edit'`, pushes an `embedding` job. 409s when
    the person is at the active-fact cap.
20. **Identity Merge review endpoint** —
    `GET /identity_merges/suggestions`, approve/reject endpoints, and
    merge application. Detection is automatic; mutation is
    user-approved.
21. **Themes layer** — migration 0020 (`themes` table, `themed_as`
    edge, `theme` kind, `active_themes_with_tier` view, universals
    backfill); universal seeding inside `insert_person`; extraction-
    LLM theme tagging via the catalog; Thread Detector emergent
    promotion with eager archetype generation; `POST /themes/{id}/
    unlock_prepare`; `apply_theme_unlock` orchestrator step that
    flips lock state on `/session/start` and propagates
    `current_theme_*` into Working Memory; producer-ranking soft
    bias (`THEME_BIAS_WEIGHT`); response generator theme blocks on
    `StarterContext` + `TurnContext`. See §7 for the full layer.

---

## 9. API contract with Node

We expose an HTTP service. Node calls us; we never call Node.

- `POST /session/start` — body: `{ session_id, person_id, role_id,
  session_metadata }`. Returns the opener message. We hydrate Working
  Memory and run the Response Generator; `metadata.taps` is always empty.
  `session_metadata` accepts optional `theme_id` (UUID) and
  `archetype_answers` (list of `{question_id, question_text,
  option_id?, option_label?, free_text?}`). When `theme_id` is
  present, the `apply_theme_unlock` orchestrator step flips the
  theme `locked → unlocked` atomically and stamps
  `current_theme_*` on Working Memory so the producer ranker can
  apply soft bias and the response generator can surface the
  theme. Answers are ephemeral priors — kept on the theme row's
  `archetype_answers` JSONB, fed into the first-turn opener
  context, but never written as moments/traits/profile_facts.
- `POST /turn` — body: `{ session_id, person_id, role_id, message }`.
  Returns the assistant reply + metadata (intent,
  emotional_temperature, taps, etc.). Runs the Turn loop end-to-end.
- `POST /session/wrap` — body: `{ session_id, person_id }`. Force-
  closes the open segment, generates session summary, kicks off
  post-session sequencing. Returns the session summary.
- `POST /admin/reset_phase` — admin-only escape hatch for Handover
  Check stickiness. Body: `{ person_id }`.
- `POST /profile_facts/upsert` — Node-driven fact edit. Body:
  `{ person_id, fact_key, answer_text, question_text? }`. Supersedes
  the prior active row, inserts new with `source='user_edit'`, pushes
  embedding. 409 at the per-person cap; 503 if
  `EMBEDDING_QUEUE_URL` is unset.
- `GET /identity_merges/suggestions?person_id=...` — list pending
  review items for entity identity corrections.
- `POST /identity_merges/scan` — manually run the identity-merge
  scanner for one subject profile. It gates candidate entity pairs with
  deterministic labels, supplies embedding distance as supporting
  context, verifies with the small LLM, and creates pending suggestions
  only.
- `POST /identity_merges/suggestions/{id}/approve` — apply a
  user-approved entity merge, repoint edges, mark source `merged`,
  update survivor aliases/description, and push re-embedding.
- `POST /identity_merges/suggestions/{id}/reject` — mark a pending
  suggestion rejected without changing graph entities.
- `POST /themes/{theme_id}/unlock_prepare` — body: `{ person_id }`.
  Returns the cached or lazily-generated archetype MC questions
  for a locked theme. Does **not** flip the theme to unlocked;
  that happens atomically inside the next `/session/start` when
  `theme_id` is carried in `session_metadata`. Repeat calls return
  cached payload at no LLM cost.
- `GET /themes/{theme_id}` — debug surface returning the theme row
  + archetype JSONB. The user-facing list of themes is read
  directly from `active_themes_with_tier` by Node.

We do **not** auth these endpoints. Node is the auth boundary.

Detailed request/response shapes live in `API.md`. Node-side
integration notes (auth, transport, async timing, what Node consumes
from the artifact queue, what Node may and may not write to Postgres)
live in `NODE_INTEGRATION.md`.

---

## 10. Conventions

- **Source of truth:** Excalidraw diagram for component shape, this
  doc + `ARCHITECTURE.md` for contracts, code for behavior. Update all
  three together.
- **Code over LLM** for orchestration. Turn Orchestrator, Phase Gate,
  Coverage Tracker, Handover Check, queue plumbing, edge writes — all
  code, not prompts. LLMs only at: Intent Classifier, Response
  Generator, Segment Detector, Extraction Worker, Trait Synthesizer,
  Thread Detector, the Producers.
- **Docs we maintain:** `CLAUDE.md` (this), `ARCHITECTURE.md`,
  `SCHEMA.md`, `QUESTION_BANK.md`, `API.md`, `NODE_INTEGRATION.md`.
- **No staging store in v1.** Extraction writes direct to canonical.
- **Rolling summary lives in Working Memory** and is regenerated at
  every segment boundary by the Segment Detector path. It is the
  agent's compressed long-term memory within a session and is
  included in the extraction queue payload. The transcript window in
  Working Memory keeps the last ~30 turns; the rolling summary
  carries everything older.
- **No entity hints, no dedicated emotional-temperature LLM in v1.**
  Emotional temperature comes from the Intent Classifier output.

---

## 11. When in doubt

- Re-read §3 (boundaries) and §4 (invariants).
- Check the Excalidraw diagram for component shape.
- Ask before adding a fourth queue, a new top-level service, or any
  cross-boundary read/write.
