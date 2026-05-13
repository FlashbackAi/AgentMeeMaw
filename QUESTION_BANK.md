# QUESTION_BANK.md — Flashback AI: Legacy Mode

This is the editorial and structural reference for every question the
agent ever asks. It covers:

- The 6 question **sources** and what each one is for.
- The **`attributes` JSONB shape** for each source (what producers
  must populate).
- The **starter anchor** set in full — wording, dimension assignment,
  and the editorial rationale behind each one.
- The **selection and ranking rules** the Phase Gate applies.
- The **embedding** strategy and how it's tied to `themes`.

For schema-level detail (column types, constraints, indexes) see
`SCHEMA.md` §2.6. For runtime architecture (when each producer fires)
see `ARCHITECTURE.md` §3.16.

---

## 1. Sources at a glance

| `source` | When it fires | Person scope | Producer |
|---|---|---|---|
| `starter_anchor` | Starter phase, session start / switch intent | **Global template** (`person_id IS NULL`) | P0 — one-time migration |
| `dropped_reference` | Inline during extraction, when a named entity is mentioned but not explored | Per-person | P1 — Extraction Worker |
| `underdeveloped_entity` | Background, **per session** post-wrap | Per-person | P2 |
| `life_period_gap` | Background, **weekly** | Per-person | P3 |
| `thread_deepen` | Inline at end of Thread Detector | Per-person | P4 |
| `universal_dimension` | Background, **weekly** | Per-person | P5 |

Only `starter_anchor` rows are global. Everything else is owned by a
single legacy.

---

## 2. The 5 anchor dimensions

The starter phase is structured around five dimensions, each chosen
to be a low-stakes, high-warmth way into a memory.

| Dimension | What it captures | Coverage Tracker credits when... |
|---|---|---|
| **sensory** | A sense memory — smell, sound, image | The extracted moment has non-empty `sensory_details` |
| **voice** | How they spoke — phrases, advice, greetings | A trait is extracted, OR a linked entity has a `saying`/`mannerism` attribute |
| **place** | The geography of their life | Any `involves` edge from the moment to a `place` entity |
| **relation** | How they related to others | Any `involves` edge to a `person` entity ≠ the subject |
| **era** | When they lived, what time felt most "them" | The moment has a `time_anchor` with a year, OR `life_period_estimate` is set |

**Selection rule** (Phase Gate, starter phase): pick the
lowest-coverage dimension; tiebreaker order is
`sensory > voice > place > relation > era`. The very first turn of a
new legacy is **always** `sensory`.

---

## 3. The `attributes` JSONB shape per source

Every question carries an `attributes` JSONB. What's in it depends on
the source. **`themes` is required on every question** (invariant
#9 — diversity ranking depends on it).

```jsonc
// starter_anchor
{
  "dimension": "sensory" | "voice" | "place" | "relation" | "era",
  "themes":    [string, ...]
}

// dropped_reference  (P1, inline in Extraction Worker)
{
  "dropped_phrase": string,    // the named entity / phrase that wasn't explored
  "themes":         [string, ...]
}

// underdeveloped_entity  (P2, per-session)
{
  "themes": [string, ...]
}

// life_period_gap  (P3, weekly)
{
  "life_period": string,       // e.g. "early career", "the years after college"
  "themes":      [string, ...]
}

// thread_deepen  (P4, inline at end of Thread Detector)
{
  "themes": [string, ...]
}

// universal_dimension  (P5, weekly)
{
  "dimension": string,         // free string from the universal-dim list
  "themes":    [string, ...]
}
```

Note that the `dimension` field is reused across `starter_anchor` and
`universal_dimension` for two different purposes:

- For `starter_anchor` it is constrained to one of the **5 anchor
  dimensions**.
- For `universal_dimension` it is one of the broader life-coverage
  dimensions (`childhood`, `family`, `work`, `marriage`, `parenthood`,
  `hobbies`, `fears`, `joys`, `regrets`, `daily_routines`, `food`,
  `beliefs`, `losses`, …).

These never collide because they're filtered by `source` first.

---

## 4. The starter anchor set (15 questions)

Three phrasings per dimension. The variation isn't decorative —
different contributors connect with different doors. The Phase Gate
picks one at random within the chosen dimension (until we have
behavioral data to do better).

### 4.1 Editorial principles

- **Status-neutral by default.** Questions must work for a living
  elder, a deceased relative, or an ancestor known through family
  stories. Mirror the contributor's tense when they use one; otherwise
  prefer tense-neutral phrasing.
- **Concrete over abstract.** "What small thing brings them to mind?"
  beats "What is their legacy?" because it gives the contributor a
  scene-sized door.
- **Warmth comes from relationship, not life status.** Use the
  contributor's relationship to the subject rather than condolence
  framing.
- **No DOB / DOD probing.** Lifespan emerges from anchored stories,
  not from front-door biographical intake.
- **Avoid "favorite," "best," "most."** Superlatives ask the
  contributor to evaluate. We want them to recall, describe, or pass on
  what they know.
- **Three of each, not five.** More variants does not help with such a
  small audience; it just dilutes wording quality. We can A/B later.

### 4.2 Sensory (3)

1. **When you picture {name}, what do you see?**
   *Visual anchor without assuming whether the picture is present-day,
   remembered, or inherited.*

2. **What small thing brings {name} to mind?**
   *A small object, sound, habit, or place is often enough to open a
   specific scene.*

3. **What is something {name} often has with {them}?**
   *Invites a signature object or practice without making the subject
   historical by default.*

### 4.3 Voice (3)

1. **What is {name} like?**
   *A compact personality/temperament opener that works for firsthand
   and inherited knowledge.*

2. **What is {name} like to talk to?**
   *Surfaces voice, rhythm, warmth, and communication style without
   assuming the conversation is no longer possible.*

3. **What word comes to mind when you think of {name}?**
   *Gives the contributor a low-friction first trait while leaving room
   for a concrete follow-up.*

### 4.4 Place (3)

1. **Where does {name} feel most rooted?**
   *Roots can be current, remembered, ancestral, or emotional.*

2. **What place should someone picture when they picture {name}?**
   *Useful for living subjects, remembered homes, and inherited family
   geography.*

3. **What is home like around {them}?**
   *Invites a scene with people, objects, and atmosphere instead of a
   profile fact alone.*

### 4.5 Relation (3)

1. **Who is {name} to you?**
   *Names the contributor's relationship without assuming a bereaved
   stance.*

2. **Who are the people closest to {name}?**
   *Draws out relational context and likely entity anchors.*

3. **Who does {name} look after, or who looks after {them}?**
   *Care can flow in either direction; this keeps the question open for
   elders, children, friends, and ancestors.*

### 4.6 Era (3)

> Era is the trickiest dimension: easy to make cold or biographical.
> These phrasings approach time through what the contributor knows.

1. **What kind of work or responsibilities shape {name}'s days?**
   *Works for employment, caregiving, household roles, study, or a
   family story about responsibility.*

2. **What part of {their} life do you know best?**
   *Allows firsthand, partial, and inherited knowledge without forcing a
   full chronology.*

3. **What years or life stage belong to the story you know?**
   *Asks for usable era context while leaving room for "I only know the
   village years" or "college days."*

---

## 5. Selection & ranking

### 5.1 Starter phase (Phase Gate)

```
1. Read persons.coverage_state.
2. Identify the dimension(s) with the lowest count.
3. Tiebreaker: sensory > voice > place > relation > era.
4. SELECT a random starter_anchor template
   WHERE attributes->>'dimension' = <chosen>
     AND status = 'active'
     AND <hasn't been answered for this person yet>.
5. If first turn ever for this legacy: force dimension = 'sensory'.
```

The "hasn't been answered yet" check uses `answered_by` edges; see
`SCHEMA.md` §7 for the query. Until that filter is wired (step 8),
the Phase Gate may legitimately re-ask a starter — that's
acceptable, as the Coverage Tracker will eventually move past
starter phase regardless.

### 5.2 Steady phase (Phase Gate)

```
1. Pull active questions for the person.
2. Score by source priority (configurable; default below) and theme
   diversity vs recently-asked questions.
3. Cap universal_dimension at 1 per top-5 (invariant #10).
4. Pick the top result.
```

**Default source priority** (highest to lowest):

```
dropped_reference > underdeveloped_entity > thread_deepen > life_period_gap > universal_dimension
```

Rationale: dropped references are the *highest-context* questions
the agent can ask — the contributor literally just said the word.
Universal questions are the lowest context and the most
survey-feeling, which is why they're capped.

### 5.3 What "themes diversity" means

When ranking the next-question slate, score down questions whose
`themes` overlap heavily with the themes of recently-asked
questions in the session. Concretely:

```
diversity_score(q) = 1 - (|themes(q) ∩ recently_asked_themes| / |themes(q)|)
```

Recently-asked = last N (~5) questions in this session. Themes is a
set, so order doesn't matter. This prevents the agent from spending
a whole session in one corner of the legacy.

---

## 6. Embedding strategy for questions

- Source field: `text` (not `text + themes`). The themes are
  metadata, not part of the semantic content the model should
  embed.
- Vector column: `embedding` (vector(1024)).
- Trigger: on insert (every producer pushes a job to the
  `embedding` queue immediately after writing the row). On
  `text` edit (rare; mostly producers write once and forget).
- Used by: P3's "embedding-aware diversity" filter when generating
  life-period-gap questions, and any future similarity-based
  ranking.

The starter set's embeddings are **not** seeded by this migration.
The embedding worker (step 3) will pick them up on first scan via:

```sql
SELECT id, text FROM questions
WHERE embedding IS NULL AND status = 'active';
```

This is the same enqueue path every other writer uses, so we're not
adding a special case for the seed. Just be aware that immediately
after this migration runs, the 15 templates have NULL embeddings —
which is fine because the Phase Gate doesn't need question
embeddings for starter selection (it filters by `dimension`, not
similarity).

---

## 7. Editing the bank

### Adding a new starter phrasing

1. Add to `0002_seed_starter_questions.up.sql` (don't make a new
   migration — this file *is* the bank).
2. Update §4 of this doc with the wording and rationale.
3. Push a re-embed if the embedding worker has already populated
   embeddings (it will pick up new NULL rows automatically; no
   special action needed).

### Removing or rewording a starter phrasing

If a phrasing is in production and being asked, treat it like any
other content edit: UPDATE the `text`, leave `id` alone, push a
re-embed job. Do **not** DELETE — historical `answered_by` edges
may reference it.

### Adding a new starter dimension

Don't, casually. The five dimensions are wired into:
- `persons.coverage_state` (default JSONB shape)
- The Coverage Tracker rules
- The Handover Check (all 5 must be ≥ 1)
- The Phase Gate tiebreaker order

Adding a sixth dimension is a real change — schema migration to add
the key, code change to the Coverage Tracker, decision about
tiebreaker placement, and a re-evaluation of every legacy already in
`steady` phase. If the system needs more coverage breadth, that's
what `universal_dimension` is for — a much lighter-weight surface.

---

## 8. What's deliberately not here in v1

- **No localization.** All starter wording is en-US. Other locales
  would need a `locale` column on `questions` and a `locale`-aware
  Phase Gate query.
- **No A/B framework.** The Phase Gate picks randomly within a
  dimension; we'll layer in tracking and selection logic once we
  have meaningful session volume.
- **No tone variants** ("warm" vs "neutral" vs "playful"). The
  current set is uniformly warm. If the contributor's role suggests
  a different register (e.g. an adult child versus a sibling),
  that's a future producer concern, not a starter-set concern.
- **No producer hints to the Response Generator.** Today the
  question text alone seeds the opener. If we want to give the
  Response Generator the editorial rationale (so it can adapt
  phrasing), we'll add an `attributes.intent` field — out of scope
  for v1.

---

*This document is content as much as code. When the wording changes,
update both the migration and §4 here in the same PR.*
