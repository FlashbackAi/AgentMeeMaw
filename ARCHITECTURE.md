# ARCHITECTURE.md — Flashback AI: Legacy Mode

This document is the system-level reference for Legacy Mode. It is the
companion to the Excalidraw diagram (the source of truth for shape) and
`SCHEMA.md` (the source of truth for table-level detail).

> **Repo scope:** This is the **Python agent service** repo. The
> Node.js Backend is a separate repo. We document the full system here
> so the agent's contract surface is clear, but only the agent
> components are implemented here.

---

## 1. System overview

```
┌──────────┐     ┌──────────────────┐     ┌─────────────────────┐
│ Frontend │ ──▶ │ Node.js Backend  │ ──▶ │  THIS REPO          │
└──────────┘     │  (separate repo) │     │  Python Agent Svc   │
                 └────┬─────────────┘     └────┬────────────────┘
                      │                        │
                ┌─────┼─────┐         ┌────────┼─────────────────┐
                ▼     ▼     ▼         ▼        ▼                 ▼
            DynamoDB  S3  Postgres  Postgres  Valkey      SQS (3 queues)
              (sess  (art  (UI      (canon-   (working    extraction
              /turns) facts)reads)  ical      memory)     embedding
                                    writes)               artifact_gen
```

Node is the gateway for every external request. Frontend never speaks
to the agent directly. The agent never speaks to DynamoDB or S3.

The agent runs three concurrent loops: Turn (synchronous), Segment
(asynchronous, per-segment), and Background (post-session and
periodic).

---

## 2. The three loops

### 2.1 Turn loop (synchronous)

Runs on every user message. Components, in order:

1. **Conversation Gateway** receives the turn from Node, hydrates
   Working Memory from Valkey.
2. **Turn Orchestrator** (code) drives the rest of the turn.
3. **Phase Gate** runs **only at session start or on a switch
   intent** — selects the question source (starter vs steady) and the
   specific anchor / next-best question.
4. **Append turn to Working Memory.**
5. **Intent Classifier** (small LLM) — outputs `intent`, `confidence`,
   `emotional_temperature`.
6. **Retrieval Service** (called only when intent demands more
   context) — fans out to graph tools.
7. **Response Generator** (big LLM) — produces the assistant reply.
8. **Append response to Working Memory.**
9. **Segment Detector** (small LLM) — runs every turn once the buffer
   threshold is crossed. Emits boundary or "not yet". On boundary,
   pushes to `extraction` queue and resets segment buffer.

The Turn loop **does not write to Postgres**. It only reads.

### 2.2 Segment loop (asynchronous)

Runs continuously in the background.

- The **Extraction Worker** drains the `extraction` queue and writes
  0–3 moments per segment, plus entities, explicit traits, edges, and
  inline P1 `dropped_reference` questions.
- It may also write **pending identity merge suggestions** when a
  contributor clarifies that two entity labels refer to the same
  person/place/object. These are review items only; no graph merge
  happens until user approval.
- For every embedded row it writes, it also pushes a job onto the
  `embedding` queue.
- For artifact-bearing rows (`persons`, `threads`, `entities`,
  `moments`), it writes the `generation_prompt` and pushes a job onto
  the `artifact_generation` queue.
- The Worker **under-extracts** — low-confidence material is dropped,
  not staged.
- Refinement / contradiction detection runs here (see §8 on edits).

After each successful extraction:

- The **Coverage Tracker** updates `persons.coverage_state`.
- The **Handover Check** flips `persons.phase` to `'steady'` if all 5
  dimensions are ≥ 1.

### 2.3 Background loop (post-session + periodic)

Triggered by Session Wrap, after the Extraction Worker drains:

```
Session Wrap
   │
   ▼
Extraction Worker drains
   │
   ▼
Trait Synthesizer
   │
   ▼
Profile Summary Generator
   │
   ▼
P2 │ P3 │ P5  (parallel — adjacency / life-period gap / universal)
```

Separately, the **Thread Detector runs on a count-based cadence** —
every 15 new active moments for the person, gated by a total of ≥ 15.
It is checked after every successful Extraction Worker run; if the
delta threshold is hit, it fires (and runs P4 inline at the end). It
is not part of the Session Wrap chain.

Coverage taps (formerly P0 / starter_anchor) are not part of this
loop. They are a fixed pool of global template questions seeded by
migration (`source='coverage_tap'`) and surfaced as archetype-style
tap cards by the Turn Orchestrator's `select_coverage_tap` step. See
§3.3 and CLAUDE.md §6.

---

## 3. Component reference

### 3.1 Conversation Gateway

Entry point for all `POST /turn` and `POST /session/start` requests
from Node. Validates request, hydrates Working Memory from Valkey
(creating it on session start), hands off to the Turn Orchestrator.

### 3.2 Turn Orchestrator

Plain code. Sequences the Turn loop steps. **Not** an LLM call.

### 3.3 Phase Gate + tap surface

Code. Three orchestrator steps cooperate to produce the next question.
Session openers no longer inline a starter question; the cold-start
load is carried by archetype answers and continuity context.

**`select_coverage_tap`** runs on every `/turn`. When intent is
`switch`/`clarify` AND some `coverage_state` dim is at 0 AND the
session cap (2) and 2-user-turn cooldown allow, it selects a global
template question from `source='coverage_tap'` for the lowest-coverage
dim (tiebreaker `era > relation > place > voice > sensory`, cold to
warm) and emits it as a structured tap.

**`select_question`** runs on switch intent when no coverage tap fired.
In `phase='steady'` it picks the top-ranked question from the produced
question bank (`dropped_reference`, `underdeveloped_entity`,
`thread_deepen`, `life_period_gap`, `universal_dimension`) ranked by
source priority and themes diversity. In `phase='starter'` the same
selector is reused but scoped to a narrower fallback set; the result
is the candidate for `promote_seeded_to_tap`.

**`promote_seeded_to_tap`** runs only when `phase='starter'`. If
`select_question` returned a seeded question and no coverage tap
fired, the seeded question is converted into a tap card so the
archetype-style surface continues mid-chat. In steady phase this
step is a no-op and the bot inlines the seeded question in its reply.

**Tap option chips** are generated per-turn by a small gpt-5.1 call
(`flashback.orchestrator.tap_options`). The call returns 4 short
option strings given the question + subject context + dimension hint.
Best-effort: on failure the card falls back to question + free-text.
Options are not persisted on the question row.

When `state.taps` is set, the Response Generator switches to a
`tap_pending` prompt branch and produces an acknowledgment only —
no question, no options. The tap card IS the next question.

### 3.4 Working Memory (Valkey)

Per-session ephemeral state. Keys scoped by `session_id`. Holds:

- **Full session transcript** (truncated to last ~30 turns).
- **Current segment buffer** — turns since last segment boundary.
- **Rolling summary** — compressed long-term context across all
  prior segments **in this session only**. Born empty at
  `/session/start`; never seeded from prior sessions. Owned by the
  Segment Detector path: on segment boundary, regenerated as a fresh
  compressed rewrite over `(prior_rolling_summary +
  closed_segment_turns)`. Never appended. Sent to the extraction
  queue payload — the Extraction Worker reads it as in-session
  context.
- **Prior session summary** — read-only cross-session context,
  seeded once at `/session/start` from `session_metadata.
  prior_session_summary` (or, when Node omits it, a continuity
  snapshot built from the canonical graph). Consumed only by the
  Response Generator so the agent can recall "last time we talked
  about X." **Never sent to the segment detector or extraction
  queue.** This split prevents previously-extracted moments from
  leaking back into extraction as if they were new in-session
  signal.
- **Signals** for the Segment Detector and Phase Gate:
  1. `turns_in_current_segment`
  2. `recent_words` (sliding window)
  3. `last_user_message_length`
  4. `emotional_temperature_estimate` (from Intent Classifier)
  5. `last_intent`
- **Last opener / last seeded question** (so the Extraction Worker
  can write `answered_by` edges).

Working memory is ephemeral. Anything that must persist is logged by
Node into DynamoDB.

### 3.5 Intent Classifier (small LLM)

Inputs: working memory signals + last few turns.

Outputs:

- `intent` — one of:
  - `clarify` — ambiguous reference, ask follow-up.
  - `recall` — user referenced something from earlier.
  - `deepen` — emotional weight is high, give space, don't probe.
  - `story` — listener is in narrative mode; let them keep going.
  - `switch` — segment is exhausted, propose a new topic.
- `confidence` — `low | medium | high`.
- `emotional_temperature` — `low | medium | high`.

Writes intent + emotional temperature into Working Memory signals.

### 3.6 Retrieval Service

Semantic (vector) retrieval surface over the canonical graph, gated
by intent per invariant #19. The orchestrator's `retrieve` step
dispatches on `effective_intent`:

| intent  | search_moments | search_entities | get_entities | get_threads | Voyage |
|---------|----------------|-----------------|--------------|-------------|--------|
| recall  | yes            | yes             | —            | —           | 2      |
| switch  | —              | —               | yes          | yes         | 0      |
| clarify | —              | —               | —            | —           | 0      |
| deepen  | —              | —               | —            | —           | 0      |
| story   | —              | —               | —            | —           | 0      |

The `query` argument to vector searches is `state.user_message`; it
is only meaningfully a retrieval anchor on `recall` (where the user
is literally referencing existing memory). `clarify` skips retrieval
entirely because vector search on ambiguous text returns noise;
`switch` uses the entity/thread catalog directly because the
SWITCH_PROMPT consumes those, not similarity-matched moments;
`deepen` and `story` skip retrieval to leave space for the user.

Tool surface:

- `search_moments(query, person_id)` — vector similarity over active
  moments. Filters by `embedding_model` + `embedding_model_version`
  (invariant #3).
- `search_entities(query, person_id)` — vector similarity over active
  entity descriptions. Same model-identity filter. Used together with
  `search_moments` on `recall` so thin-entity mentions (a name dropped
  once, never fleshed out) still surface.
- `get_entities(person_id)` — full active-entity catalog for the
  legacy.
- `get_entities_by_ids(person_id, entity_ids)` — fetch entities by
  id, scoped to a person. Used by the entity-mention scanner
  (§3.6a) which already knows the ids it wants and just needs the
  full descriptions.
- `get_related_moments(entity_id)` — graph traversal: moments linked
  to this entity via `involves`.
- `get_threads(person_id)` — threads for the legacy.
- `get_threads_for_entity(entity_id)` — threads this entity is
  evidence for.
- `get_threads_summary(person_id)` — short summary across threads.
- `get_dropped_phrases_for_session(session_id)` — open
  `dropped_reference` questions seeded this session.
- `get_session_summary(session_id)` — the per-session recap from
  Session Wrap.

Every call filters by `person_id` and `status='active'`.

### 3.6a Entity Mention Scanner

Deterministic, intent-independent retrieval surface that sits between
the Intent Classifier and the Retrieval Service (invariant #20). The
agent's most common real-world pattern is the contributor mentioning
a known entity mid-narrative ("Chaitanya called me yesterday"); on
`story` turns the intent-gated matrix would skip retrieval entirely
and miss the reference. The scanner closes that gap without burning
a Voyage call.

Pipeline:

- **Cache.** `entity_names:{person_id}` in Valkey, JSON list of
  `{id, name, aliases[], kind}`. Loaded by `EntityNameCache.get`
  cache-aside: read on every user turn, repopulate from
  `active_entities` on miss. Kind filter excludes `object`
  entities — common nouns ("bottle", "table") would false-positive
  too often.
- **Matcher.** Word-boundary regex (`\b(name|alias|...)\b`,
  case-insensitive) against `user_message`. Entries are tested in
  descending name-length order so "Chaitanya Reddy" wins over a
  bare "Chaitanya" alias when both could match the same span. One
  hit per entry per turn.
- **Disambiguation flag.** If the same matched surface form (e.g.
  "Priya") resolves to two or more distinct entity ids, the step
  sets `state.ambiguous_mention = True`. The response generator's
  context renders `<mentioned_entities ambiguous="true">` so the
  prompt can ask the contributor to disambiguate. Deepen-intent
  turns still win — the disambiguation waits.
- **Invalidation.** The Extraction Worker holds a sync
  `redis.Redis` client (separate from the agent's async client)
  and `DEL`s the cache key after entity rows commit, so newly
  extracted entities become scannable on the next user turn
  without waiting for TTL. Best-effort: cache hygiene failures are
  logged and swallowed; the graph state remains correct.

The output (`state.mentioned_entities`) flows into the response
generator's context as a `<mentioned_entities>` block alongside the
semantic retrieval blocks; the two surfaces are orthogonal and can
both populate on the same turn (e.g. `recall` plus a name mention).

### 3.7 Response Generator (big LLM)

Inputs: system prompt, memory context (compressed: subject + role +
key facts), retrieval results, the seeded question (if any).

Output behavior is intent-driven:

- **`clarify` / `recall`** — minimal probing and validation.
- **`deepen`** — ask more specific sensory questions.
- **`story`** — short reflective acknowledgement; let the user
  continue.
- **`switch`** — propose 2–3 directions and let the user pick.

The starter opener is a special case: must (a) name the deceased,
(b) name the Flashback role, (c) ask the chosen anchor.

### 3.8 Segment Detector (small LLM)

Runs after every turn once the buffer threshold is crossed (configured
constant, tuned empirically). Decides whether the segment is still
moving or has closed.

- **Output: boundary detected** → execute the boundary path:
  1. Regenerate the **rolling summary** (small LLM call) over
     `(prior_rolling_summary + closed_segment_turns)`. This is a
     fresh compressed rewrite, not an append.
  2. Push to the `extraction` queue with payload:
     ```
     {
       session_id, person_id,
       segment_turns,
       rolling_summary,            // the new one
       prior_rolling_summary,      // for diff/audit
       seeded_question_id          // if any
     }
     ```
  3. Replace the rolling summary in Working Memory with the new one.
  4. Reset the segment buffer in Working Memory.
- **Output: not yet** → leave the buffer and rolling summary alone.

Session Wrap calls this with `force=true`, which always closes the
open segment, regenerates the rolling summary, and pushes to the
queue.

The rolling-summary regeneration can be implemented as a second
small-LLM call on the boundary path or folded into a single Segment
Detector prompt that emits both `boundary_decision` and
`updated_rolling_summary` when the decision is "boundary." Either is
acceptable; we'll pick the cleaner option in step 10.

### 3.9 Extraction Worker (big LLM)

Drains the `extraction` queue. Per closed segment, it has access to:

- The **segment turns** (the closed conversation slice).
- The **rolling summary** at the time the segment closed (compressed
  prior context for everything that came before in the session).
- The **seeded question** (if any).

The rolling summary is **context**, not source — moments are extracted
from the segment turns; the rolling summary is there to disambiguate
references, resolve entity continuity, and avoid re-extracting things
that have already been captured.

Per closed segment, the Worker performs:

1. **Generate the segment into a coherent moment** — title, narrative,
   `time_anchor`, `life_period_estimate`, `sensory_details`,
   `emotional_tone`, `contributor_perspective`.
2. **Extract candidate entities** with type-specific attributes
   (incl. relationships, places, objects, organizations).
3. **Resolve mentions** to existing entities where possible (vector
   similarity + alias check + rolling-summary cross-reference).
4. **Refinement / contradiction detection** — vector search + entity
   overlap + LLM compatibility check against existing moments. See
   §8.
5. **Persist to canonical graph** — moments, entities, edges
   (`involves`, `happened_at`, `related_to`, `themed_as`), explicit
   traits with `mentioned_once` strength.
6. **Write `answered_by`** edges if the segment was seeded by a known
   question.
7. **Inline P1 `dropped_reference`** — when a named person/place/
   object is mentioned but not explored, write a question with
   `source='dropped_reference'`, `attributes.dropped_phrase` set.
8. **Create pending identity merge suggestions** — when the extractor
   emits a canonical entity with an alias matching another active
   entity name for the same person, write a pending review row. Example:
   canonical `Person B` with alias `old label for Person B` suggests
   merging the existing `old label for Person B` entity into `Person B`.
9. **Push embedding jobs** for every embedded row written or changed.
10. **Push artifact jobs** for every newly created person / thread /
   entity / moment, with the `generation_prompt` persisted alongside.
11. **Check the Thread Detector trigger** — if
    `count(active_moments) - moments_at_last_thread_run ≥ 15` and
    total ≥ 15, enqueue a Thread Detector run.

The Worker is intentionally conservative — under-extract rather than
over-extract.

**Theme tagging.** Just before the LLM call, the worker fetches
`active_themes` for the subject (universals + emergents) and
renders the catalog as `<theme_catalog>` in the user message. The
LLM populates `moment.themes: [<slug>]` per moment as an optional
output field; persistence resolves slugs through a `{slug:
theme_id}` map captured at fetch time and writes one `themed_as`
edge per resolvable slug. Unknown slugs are dropped silently per
invariant #6. See §3.16a for the full Themes layer.

### 3.10 Coverage Tracker

Code, runs after the Extraction Worker per moment. Increments
`persons.coverage_state` according to the rules in `CLAUDE.md` §6.

### 3.11 Handover Check

Code. After Coverage Tracker, if every dimension is ≥ 1, set
`persons.phase = 'steady'` and stamp `phase_locked_at`. Sticky.

### 3.12 Embedding Pipeline

The embedding pipeline is **its own subsystem**. Detailed in §6.

### 3.13 Thread Detector

Runs on a **count-based cadence**: every 15 new active moments for the
person. Specifically:

- **Gate:** total active moments for the person ≥ 15.
- **Trigger:** `count(active_moments) - moments_at_last_thread_run ≥ 15`.
- The Extraction Worker checks this after every successful run and
  enqueues a Thread Detector job when the trigger fires.
- After the Thread Detector completes, it updates
  `persons.moments_at_last_thread_run` to the current active-moment
  count.

This means the cadence is **driven by activity, not wall-clock time**.
A power user generating many moments triggers it more often; a quiet
legacy doesn't burn cycles for nothing.

Algorithm:

1. **Clustering**
   - Pull all active moments for the person from the canonical graph.
   - Get each moment's `narrative_embedding`.
   - Run a clustering algorithm (e.g., HDBSCAN or k-means with
     silhouette tuning).
   - Output: clusters of semantically related moments.
2. **Thread matching** — for each cluster:
   - Compute the centroid of the cluster's embedding.
   - Compare to existing thread embeddings.
   - **High similarity** → link new moments to the existing thread.
   - **Partial match** → flag for review.
   - **No match** → run an LLM call on the cluster moments to propose
     a new thread (name + description), then write a thread row with
     `source='auto-detected'` plus a confidence score, and link
     constituent moments via `evidences` edges.
3. **Trigger P4 inline** at the end — for each detected/updated
   thread, generate questions that would surface new info or deepen
   the thread; tag `thread_deepen`.
4. **Emergent theme decision (new-thread path only).** The naming
   LLM tool gains three optional fields: `theme_display_name`
   (2–4 word phrase), `theme_slug` (snake_case), `theme_description`
   (one sentence). The prompt instructs the LLM to set these only
   when the cluster is a discrete passion / practice / place that
   universals don't already cover. When all three are set, the
   worker eagerly generates archetype MC questions (small LLM
   call, outside the transaction), then inside the existing per-
   cluster tx inserts an `emergent` `themes` row linked to the new
   thread (carrying the cached `archetype_questions` JSONB), and
   writes `themed_as` edges from every cluster member moment to
   the new theme. On the existing-match path (cluster latched
   onto a prior thread), the worker looks up that thread's
   emergent theme (if any) and back-tags the new cluster's
   moments to it — covering the gap where extraction couldn't
   see the emergent yet because the cluster predated it.
5. Update `persons.moments_at_last_thread_run`.

### 3.14 Trait Synthesizer (small LLM)

Looks at available traits and existing threads. Two operations:

- **"Does this set of threads suggest a trait that doesn't yet
  exist?"** → create a new trait.
- **"Do these threads strengthen an existing trait?"** → upgrade
  strength along the ladder:

  ```
  mentioned_once → moderate → strong → defining
  ```

Links evidence via `exemplifies` and `evidences` edges.

### 3.15 Profile Summary Generator

Generates a compact profile of the subject. Surface:

1. Display name + relationship.
2. Top 5–7 traits by strength.
3. A few key threads.
4. Time period of life (derived from moment time anchors).
5. Key entities (close family, important places).

Stored on the `persons` row. Regenerated at the end of each session
inside the Background loop.

### 3.16 Question Producers

| Producer | Trigger | Source tag |
|---|---|---|
| **P0** (retired) | One-time seeder migration, relabelled by 0019 | `coverage_tap` |
| **P1** | Inline in Extraction Worker | `dropped_reference` |
| **P2** | Background loop, **per session** | `underdeveloped_entity` |
| **P3** | Background loop, **weekly** | `life_period_gap` |
| **P4** | Inline at end of Thread Detector | `thread_deepen` |
| **P5** | Background loop, **weekly** | `universal_dimension` |

P0 still exists as data — migration 0019 relabelled the original
`starter_anchor` rows to `coverage_tap` so the runtime tap surface
draws from them. There is no producer process running P0 at runtime;
the rows are a fixed seed.

#### P2 — Underdeveloped entity (per session)

1. Pull all entities for the person from the canonical graph.
2. For each entity, compute density signals:
   `(count_of_moments, avg_narrative_len, mentions_in_summary)`.
3. Threshold: `dropped_reference < 3 moments`.
4. Score weighting: short narrative_len, low mentions →
   under-developed.
5. For each entity below the threshold, generate 1–2 targeted
   questions (look at related threads for intersection).
6. Tag `underdeveloped_entity`. Store with `targets` edge to the
   entity.

#### P3 — Life-period gap (weekly)

1. Compute the lifespan window from the **moment time anchors**
   (DOB/DOD are deliberately not stored).
2. Bucket existing memories by decade or life-stage.
3. Identify buckets with 0 records.
4. For each underrepresented bucket, generate 3–5 questions (with
   embedding-aware diversity) about that life period. Phrase
   generically: *"This was around the time after college — were they
   working, or starting something new?"*
5. Tag `life_period_gap`; store with `attributes.life_period` set.

#### P5 — Universal coverage (weekly)

1. Walk the universal dimension list — childhood, family, education,
   work, marriage, parenthood, hobbies, fears, joys, regrets, advice,
   daily routines, food, beliefs, memorable phrases, faiths, big
   losses, etc.
2. Identify dimensions with `< 3` moments (or no moments at all).
3. For each, generate 1–2 questions.
4. Tag `universal_dimension`; store with `attributes.dimension` set.

#### Question ranking (for steady-phase Phase Gate)

Pull active questions for the person, rank by source priority, theme
diversity, and recency. **Cap `universal_dimension` at 1 per top-5**
to avoid the survey feel.

`combined_score = source_priority + DIVERSITY_WEIGHT * diversity
+ THEME_BIAS_WEIGHT * theme_bias`. `theme_bias_score` returns 1.0
when the candidate's `attributes.themes` overlaps the active
deepen-session `current_theme_slug` (read off Working Memory by
the `select_question` step), 0.0 otherwise. `THEME_BIAS_WEIGHT =
1.5` — enough to break ties in favor of theme-aligned questions,
not enough to override a high-priority `dropped_reference` on a
different theme (priority gap = 4.0). Soft bias only; the
producer ranker never hard-filters by theme. When no theme is
active the bias term is 0 and ranking behaves as before.

### 3.16a Themes layer (user-facing)

User-visible thematic groupings of moments. The hard rules live in
`CLAUDE.md` invariant #22 + §7. Architectural shape:

**Storage.** `themes` table holds two kinds of rows:

- `kind='universal'` — five rows seeded at person creation
  (`family`, `career`, `friendships`, `beliefs`, `milestones`).
  `thread_id` is NULL.
- `kind='emergent'` — written by the Thread Detector when a new
  thread maps to a discrete passion / practice that universals
  don't cover. `thread_id` points to the originating thread
  (1:1 in v1; no merging or splitting later).

Both start `state='locked'`. Partial unique index on
`(person_id, slug) WHERE status='active'` makes seeding and the
back-tag path idempotent. `archetype_questions` (JSONB) and
`archetype_answers` (JSONB) live on the row. The
`active_themes_with_tier` view denormalises tier + counters
(`qualifying_count`, `life_period_count`, `has_rich_sensory`) off
the live `themed_as` × `active_moments` join — see CLAUDE.md §7.5
for the rules. Tier is never persisted.

**Edge.** `themed_as` (moment → theme). Multi-tag expected: one
moment may carry both `family` and `milestones`. Supersession
already drops outbound edges from a superseded moment as part of
the existing tx, so refined moments re-acquire tags from the
fresh LLM emission.

**Writers.**

- **Extraction Worker** — primary tagging surface. The LLM emits
  `moment.themes: [<slug>]` from a `<theme_catalog>` injected
  into the user message; persistence writes `themed_as` edges
  via the slug→id map captured at fetch time. See §3.9.
- **Thread Detector** — emergent theme creation + back-tagging
  on the new-thread path; back-tagging only on the existing-match
  path when a prior thread already has a theme. See §3.13.
- **`persons.repository.insert_person`** — seeds the five
  universals in the same transaction as the persons row.

**Unlock flow.**

1. UI taps a `locked` theme → `POST /themes/{id}/unlock_prepare`.
2. Agent returns archetype questions; lazy-generates + caches on
   the row when `archetype_questions IS NULL` (universals path).
   Emergents have these cached eagerly at promotion time.
3. UI shows 3–4 MC questions × 4 chips each (skip + free-text
   per question; mirrors onboarding).
4. UI calls `POST /session/start` with `session_metadata.
   theme_id` and `session_metadata.archetype_answers`.
5. The new `apply_theme_unlock` orchestrator step flips the
   theme `locked → unlocked` atomically, persists answers as
   JSONB (ephemeral priors only — never written as moments,
   traits, or profile facts), and stamps `current_theme_id` /
   `current_theme_slug` / `current_theme_display_name` on
   Working Memory.
6. The opener context carries a `<current_theme>` block plus
   the archetype answers. The bot acknowledges the focus
   without restarting from scratch.

**Deepen flow.** When the user taps an already-`unlocked` theme,
the same `/session/start` path runs with `theme_id` but no
`archetype_answers`. `apply_theme_unlock` is a no-op on the
state flip but still stamps `current_theme_*` on Working
Memory. The producer ranker's soft bias and the response
generator's theme awareness do the rest.

**Reads.** Node consumes `active_themes_with_tier` directly from
Postgres per the integration contract. The agent does not expose a
GET themes list — the read surface is the view. Local dev's
`/memories` mirrors the same query, and self-heals by lazily
seeding universals when it observes zero active themes for a
person.

### 3.17 Session Wrap

Triggered on `POST /session/wrap`. Steps:

1. **Force-close any open segment** by pushing the Segment Detector
   with `force=true`. This regenerates the rolling summary one final
   time and pushes the segment to the `extraction` queue. This is the
   only mechanism for tail flush.
2. **Generate a session summary** — 2–3 sentences. Stored on the
   session record (in DynamoDB via Node — we return it on the wrap
   response). Used as context next session: *"Last time you talked
   about…"* The session summary is a separate, more compact artifact
   than the rolling summary; the next session's rolling summary is
   seeded from it.
3. **Invoke the post-session sequence** (in order): Extraction Worker
   drains → Trait Synthesizer → Profile Summary → P2/P3/P5 in
   parallel.

The Thread Detector is **not** invoked by Session Wrap directly. It
runs on its own count-based cadence (see §3.13), checked by the
Extraction Worker after each run.

---

## 4. Data ownership

| Concern | Owner | Store |
|---|---|---|
| Auth, users, contributor `person_roles` | Node | (Node tables) |
| Sessions and per-turn transcript log | Node | DynamoDB |
| Canonical graph reads (UI surfaces) | Node | Postgres (read-only) |
| Canonical graph writes | **Agent** | Postgres |
| Working Memory (per-session ephemeral) | **Agent** | Valkey |
| Embedding generation | **Agent** (embedding worker) | SQS → Postgres |
| Artifact generation (image/video) | Node consumer | SQS → S3 → Postgres URLs |
| Pushing onto any queue | **Agent** | SQS |

The agent receives `session_id`, `person_id`, `role_id`, and session
metadata from Node — either passed in on each request or fetched via a
Node API.

---

## 5. Storage model — the hybrid graph

### 5.1 Why hybrid

A pure relational schema needed a link table for every relationship
type plus `evidence_*_ids` arrays scattered everywhere. A pure graph
store was overkill. The hybrid keeps strongly-typed node tables and
collapses every relationship into one generic `edges` table.

### 5.2 Node tables

- **`persons`** — the subject. One row per legacy. Includes `phase`,
  `coverage_state`, `phase_locked_at`, `moments_at_last_thread_run`,
  `image_url`, `generation_prompt`. **No** `date_of_birth`, **no**
  `date_of_death`.
- **`moments`** — discrete recalled episodes. `title`, `narrative`,
  `time_anchor`, `life_period_estimate`, `sensory_details`,
  `emotional_tone`, `contributor_perspective`, `video_url`,
  `thumbnail_url`, `generation_prompt`, `status`,
  `narrative_embedding`, `embedding_model`, `embedding_model_version`.
- **`entities`** — people / places / objects / organizations. Sub-typed
  via `kind`; type-specific `attributes` JSONB; `aliases`;
  `description`, `description_embedding`, embedding model columns;
  `image_url`, `generation_prompt`.
- **`threads`** — emergent narrative arcs across moments. `name`,
  `description`, `description_embedding`, `source`, `confidence`,
  `image_url`, `generation_prompt`.
- **`traits`** — character properties of the subject. `name`,
  `description`, `description_embedding`, `strength`
  (`mentioned_once | moderate | strong | defining`).
- **`questions`** — first-class. `text`, `embedding`, `source`,
  `attributes` JSONB (`dropped_phrase`, `life_period`, `dimension`,
  `themes`), `status`.
- **`themes`** — user-facing thematic groupings of moments. `kind`
  (`universal | emergent`), `slug` (snake_case; partial unique on
  active rows per person), `display_name`, `description`, `state`
  (`locked | unlocked`), `archetype_questions` JSONB,
  `archetype_answers` JSONB, `unlocked_at`, `thread_id` (set for
  emergents, NULL for universals), `image_url`, `generation_prompt`,
  `status`. The `active_themes_with_tier` view denormalises tier
  + counters from `themed_as` × `active_moments`. See §3.16a.
- **`profile_facts`** — flat (question, answer) records on the
  legacy profile. `fact_key` (snake_case, free-form slug),
  `question_text`, `answer_text`, `source`, `answer_embedding`,
  embedding model columns, `status`. Cap = 25 active per person.

History tables (`moment_history`, optionally others) capture user
manual edits for audit.

### 5.3 The generic `edges` table

```
edges (
  id, from_kind, from_id, to_kind, to_id, edge_type,
  attributes JSONB, status, created_at, ...
)
```

Edge types:

- `involves` — moment ↔ entity (role in `attributes`).
- `happened_at` — moment ↔ time anchor / place entity.
- `exemplifies` — moment ↔ trait.
- `evidences` — moment ↔ thread, entity ↔ thread, thread ↔ trait,
  entity ↔ trait.
- `related_to` — entity ↔ entity.
- `motivated_by` — question ↔ moment / entity / thread that prompted
  it.
- `targets` — question ↔ entity it's asking about.
- `answered_by` — question ↔ moment(s) extracted from the segment that
  question seeded.
- `themed_as` — moment ↔ theme. Multi-tag expected.

`validate_edge()` in app code enforces which `from_kind`/`to_kind`
combinations are valid for each `edge_type`. The full kind enum is
`{moment, entity, thread, trait, question, person, theme}`.

### 5.4 Supersession and merges

- **Supersession** (refinement): old row → `status='superseded'`, new
  row → `status='active'`. **All edges pointing at the old row are
  repointed to the new row in the same transaction.**
- **Merge** (entity dedup): aliases moved to surviving entity, all
  edges repointed, losing entity → `status='merged'`. In production,
  this is user-approved through `identity_merge_suggestions`; extraction
  can propose but cannot directly merge.
- **User edit**: in-place update + `moment_history` row capturing the
  before-state, editor, and timestamp. Edges unchanged.

Queries always filter `status='active'` (use the `active_*` views).

### 5.5 Embeddings

`vector(1024)` on embedded rows. `embedding_model` and
`embedding_model_version` alongside, on every embedded row. Mixing
models is forbidden. See §6.

---

## 6. Embedding pipeline

The embedding pipeline is an asynchronous subsystem. All embedded rows
are written to Postgres **without** their vector column populated;
the embedding worker fills it in.

### 6.1 What gets stored

| Record type | Source field | Stored as |
|---|---|---|
| Moment | `narrative` | `narrative_embedding` |
| Entity | `description` | `description_embedding` |
| Thread | `name + ", " + description` | `description_embedding` |
| Trait | `name + ", " + description` | `description_embedding` |
| Question (bank) | `text` | `embedding` |

### 6.2 When triggers fire

| Event | Re-embed |
|---|---|
| Moment created (Extraction Worker) | `narrative_embedding` |
| Moment narrative edited (user) | `narrative_embedding` |
| Entity created (Extraction Worker) | `description_embedding` |
| Entity description meaningfully changed | `description_embedding` |
| Entity merged (primary entity) | `description_embedding` (aliases / desc grow) |
| Thread created (Thread Detector) | `description_embedding` |
| Thread description refreshed | `description_embedding` |
| Trait created / strength upgraded | `description_embedding` |
| Question added to bank | `embedding` |

### 6.3 Flow

```
Writer (Extraction Worker / Thread Detector / Trait Synth / Producer)
   │
   ├── INSERT/UPDATE row in Postgres (vector column NULL)
   │   with embedding_model + embedding_model_version columns set
   │
   └── push to SQS:embedding
         {
           record_type, record_id, source_text,
           embedding_model, embedding_model_version
         }
              │
              ▼
        Embedding Worker
              │
              ├── call Voyage API
              └── UPDATE Postgres SET <vector column> = ...
                  WHERE record_id = ... AND embedding_model_version = ...
```

The version guard on update avoids stomping on a row whose model has
been upgraded between enqueue and write.

### 6.4 Rules

- **Never inline.** Always via the queue.
- **Never mix models.** Re-embed on model change; do not silently
  coexist.
- **Always filter `person_id`** when querying by similarity (joins
  through the node table).
- **Always filter `status='active'`** on similarity queries.

---

## 7. Queues

Three SQS queues; each has its own worker.

- **`extraction`** — segment-level jobs from Segment Detector or
  Session Wrap. Drained by the Extraction Worker. Payload:
  ```
  {
    session_id, person_id,
    segment_turns,
    rolling_summary,            // freshly regenerated at boundary
    prior_rolling_summary,      // for diff/audit
    seeded_question_id          // if any
  }
  ```
- **`embedding`** — per-row jobs pushed by every writer. Drained by
  the embedding worker (this repo). Payload:
  ```
  {
    record_type, record_id, source_text,
    embedding_model, embedding_model_version
  }
  ```
- **`artifact_generation`** — per-row jobs pushed by the agent when it
  creates an artifact-bearing row. Body includes `entity_type`,
  `entity_id`, `prompt`, `metadata`. Drained by **Node** (separate
  repo), which calls the generation model, uploads to S3, writes URL
  columns.

---

## 8. Edits, refinements, merges

Three triggers for changes to existing rows.

### A. Auto-detected refinement / contradiction (Extraction Worker)

For every newly extracted moment, run:

1. Vector search over existing active moments for the person.
2. Entity-overlap filter on the candidates.
3. LLM compatibility check on each survivor.

Verdicts:

- **Refinement** → supersession. Old row → `status='superseded'`, new
  row → `status='active'`, all edges repointed in the same
  transaction. Re-embed the new row.
- **Contradiction** → both kept active, conflict logged (e.g., as a
  thread or annotation for review).
- **Independent** → just add the new moment.

### B. User manual edit (via Node)

In-place update on the canonical row, with a `moment_history` row
capturing the previous values, editor, and timestamp. Edges unchanged.
If the edited field is embedded, push a re-embed job.

### C. User-approved identity merge

Identity merges are a two-step workflow:

1. **Detection** — the Extraction Worker may create a pending
   `identity_merge_suggestions` row when a newly extracted canonical
   entity carries an alias that matches an existing active entity name.
   Example: after a contributor says an earlier label refers to a named
   person, extraction emits the named person as canonical and places the
   earlier label in `aliases`; persistence proposes earlier-label →
   canonical-name. A background/manual scanner can also search existing
   active entities under the same subject profile using deterministic
   labels, with embedding distance supplied as supporting context, then
   spend a small LLM verifier call only on gated candidate pairs.
2. **Approval** — Node/UI surfaces the suggestion out-of-band
   (toast/review surface), not inside the memorial chat. Only
   `POST /identity_merges/suggestions/{id}/approve` performs the merge.

On approval:

- aliases from the losing entity are moved to the surviving entity;
- all inbound/outbound entity edges are repointed to the survivor;
- losing entity → `status='merged'`, `merged_into=<survivor>`;
- survivor's embedding fields are cleared and a fresh embedding job is
  pushed.

Rejecting a suggestion marks it `status='rejected'` and leaves the graph
unchanged.

Traits editing UX is **deferred from v1**.

---

## 9. Extraction surface (per segment)

What the Extraction Worker is allowed to write per segment:

- **0–3 moments**, with: `title`, `narrative`, `time_anchor`,
  `life_period_estimate`, `sensory_details`, `emotional_tone`,
  `contributor_perspective`.
- **Entities** (4 sub-types: `person`, `place`, `object`,
  `organization`), each with type-specific `attributes` JSONB and
  `aliases`.
- **Edges**: `involves`, `happened_at`, `related_to`.
- **Explicit traits only** with `mentioned_once` strength. (Higher
  strengths are derived later by the Trait Synthesizer.)
- **`answered_by`** edges to the seeding question, if known.
- **Inline P1 `dropped_reference` questions** — when a named entity is
  mentioned but unexplored, write a question with
  `source='dropped_reference'`, `attributes.dropped_phrase` set.

Subject identity reminder: the subject is in `persons`, never
duplicated into `entities`.

---

## 10. Lifecycle of a turn

```
Frontend         Node Backend          Agent (Turn loop)         Postgres / Valkey / SQS
   │                  │                      │
   │── message ─────▶│                      │
   │                  │── POST /turn ──────▶ │
   │                  │                      │── load WM ──────▶ Valkey
   │                  │                      │   (transcript, segment buf,
   │                  │                      │    rolling summary, signals)
   │                  │                      │
   │                  │                      │── (if start)
   │                  │                      │   Phase Gate ─── pick Q
   │                  │                      │
   │                  │                      │── append turn ─▶ Valkey
   │                  │                      │── Intent Classifier (LLM)
   │                  │                      │── Entity Mention Scan (det.)
   │                  │                      │── Retrieval (intent-gated) ─▶ Postgres (read)
   │                  │                      │── Response Generator (LLM)
   │                  │                      │── append response ─▶ Valkey
   │                  │                      │
   │                  │                      │── (if buffer ≥ T)
   │                  │                      │   Segment Detector (LLM)
   │                  │                      │── (if boundary)
   │                  │                      │   regenerate rolling summary (LLM)
   │                  │                      │   write rolling summary ─▶ Valkey
   │                  │                      │   enqueue {segment, rolling_summary}
   │                  │                      │     ─────────▶ SQS:extraction
   │                  │                      │   reset segment buffer
   │                  │◀── reply, metadata ──│
   │                  │── log turn ─────────▶ DynamoDB
   │◀── reply ────────│
```

---

## 11. Lifecycle of a session

```
... many turns ...

User ends session ──▶ Node ──▶ POST /session/wrap
                                  │
                                  ├── Segment Detector (force=true)
                                  │   ├── regenerate rolling summary
                                  │   └── enqueue final segment ──▶ SQS:extraction
                                  │       (with rolling_summary)
                                  │
                                  │  (Extraction Worker drains queue —
                                  │   writes moments / entities / traits,
                                  │   pushes embedding + artifact jobs,
                                  │   checks Thread Detector trigger ─┐ )
                                  │                                    │
                                  ├── Coverage Tracker (per moment)    │
                                  ├── Handover Check                   │
                                  │                                    │
                                  ├── Trait Synthesizer                │
                                  │                                    │
                                  ├── Profile Summary Generator        │
                                  │                                    │
                                  └── P2 │ P3 │ P5  (parallel)         │
                                                                       │
        Independently, when                                            │
        15+ new active moments accumulate ◀────────────────────────────┘
                  │
                  ▼
            Thread Detector
                  │
                  └── P4 inline at end

                                  Returns: { session_summary, ... } ──▶ Node
                                                                          │
                                                                          ▼
                                                                       DynamoDB
                                                                       (session record)
```

---

## 12. Artifact generation pipeline

Stylized (Pixar-ish) visuals are part of v1. Pipeline:

```
Agent creates row (person/thread/entity/moment)
   │
   ├── INSERT row in Postgres with `generation_prompt`
   │   (image_url / video_url left NULL)
   │
   └── push to SQS:artifact_generation
         { entity_type, entity_id, prompt, metadata }
              │
              ▼
         Node consumer (separate repo)
              │
              ├── calls generation model
              ├── uploads to S3
              └── UPDATE Postgres SET image_url|video_url|thumbnail_url = ...
```

The agent **only** writes the prompt and enqueues. The agent **never**
touches S3 or the URL columns. Node is the only writer of those
columns.

---

## 13. What's deferred from v1

Out of scope for the first release:

- Voice features (input or output) — entirely deferred.
- Photoreal video of the deceased.
- "Talk to dad" impersonation chatbot.
- Traits editing UX.
- Staging store for low-confidence extractions.
- Entity hints surface.
- Dedicated emotional-temperature LLM (the Intent Classifier supplies
  `emotional_temperature`).
- Multi-contributor workflows beyond the basic role model.

The **rolling summary** is **in v1** — owned by the Segment Detector
path, stored in Working Memory, included in the extraction queue
payload. See §3.4 and §3.8.

---

## 14. Open questions / TBD

- Concrete buffer threshold for Segment Detector activation —
  empirically tuned.
- Concrete generation model for stylized images and short videos.
- Production UI polish for merge approvals beyond the local toast.
- Observability: structured logging across the three loops, plus
  per-loop latency/error dashboards.
- Clustering algorithm choice for the Thread Detector (HDBSCAN vs.
  k-means with silhouette tuning).

---

*This document evolves with the system. When the Excalidraw diagram or
schema changes shape, update here in the same PR.*
