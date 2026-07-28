# Question feed → tap to start a seeded session

**Date:** 2026-07-07
**Status:** design approved, pre-implementation
**Repo:** Python agent service (this repo). Node/frontend rendering is out of scope.

## Problem

We already build a per-person bank of questions (the producer bank: P2/P3/P5
plus the inline producers). Today those questions only surface implicitly — the
orchestrator picks one to seed an opener or inline into a reply. There is no way
for the contributor to *browse* the questions we're holding for a legacy.

We want to surface those questions in the scrolling feed. Tapping a question
starts a conversation seeded with that question as context.

## Goals

- A browsable, ranked list of the producer-bank questions for a person.
- Tapping one starts a session whose opener grounds on that exact question.
- Reuse the existing ranking + decision-filtering logic — no duplicated ranking,
  no new LLM calls.

## Non-goals (YAGNI)

- Pagination, feed caching, a separate "seen/answered" tracker
  (`answered_by` edges + `question_decisions` already handle suppression).
- Coverage-tap and archetype/unlock questions in the feed — they keep their own
  surfaces.
- Any Node/frontend rendering work (separate repo).

## Decisions (settled during brainstorming)

- **Feed source:** a new agent endpoint (ranking is agent-side computation — the
  [API.md](../../../API.md) §9 carve-out), not Node reading `active_questions`
  raw.
- **Feed breadth:** producer-bank sources only —
  `dropped_reference`, `underdeveloped_entity`, `thread_deepen`,
  `life_period_gap`, `universal_dimension`.
- **Feed ordering:** the full `combined_score` (source priority + recency +
  defer boost) — the same order the bot uses to decide what to ask next, so the
  feed's top item matches the bot's instinct.
- **Tap is an explicit pick:** it is exempt from the same-source cooldown and
  recency dedup. We always honor the exact question tapped.

---

## Piece 1 — `GET /questions/feed`

### Contract

```
GET /questions/feed?person_id=<uuid>&limit=<int, default 25, max 50>
```

Response:

```jsonc
{
  "questions": [
    {
      "question_id": "uuid",
      "text": "string",
      "source": "dropped_reference | underdeveloped_entity | thread_deepen | life_period_gap | universal_dimension",
      "themes": ["family", "..."],   // from attributes.themes
      "created_at": "iso-8601"
    }
  ]
}
```

- Person-scoped only; no session required.
- `person_id` missing/unknown → 400 / empty list per existing route conventions
  (mirror the other person-scoped GETs).

### Implementation

1. **Shared ranking helper.** Extract the score-and-sort core out of
   `SteadySelector.select` (`phase_gate/steady_selector.py`) into a helper —
   `rank_candidates(candidates, *, recent_themes, active_theme_slug, now)` —
   returning the full list of `_ScoredCandidate` sorted by
   `(score, created_at)` desc. `select()` keeps calling it and then applies the
   universal-dimension demotion to pick one; the feed calls it and returns many.
   No behavior change to the single-pick path.

2. **Candidate fetch.** Reuse `SELECT_STEADY_CANDIDATES` with
   `sources = PRODUCER_SOURCES`, `exclude_skipped=True` (so `skip`/`suppress`
   decisions are honored; `defer` stays in and keeps its boost), and
   `recent_ids = []` — the feed has no session cooldown context, and browse is
   not "recently asked."

3. **Diversity.** Apply invariant #10 across the returned slice: at most one
   `universal_dimension` question per 5 positions. Implement as a post-sort
   interleave pass over the ranked list, not a hard filter (don't drop the
   others, just push them down so they don't cluster).

4. **Cap** at `limit` (default 25, clamp to 50).

5. **New feed method** lives on a small `QuestionFeed` service (or a function in
   `phase_gate/`) that owns the fetch + rank + diversity + cap. Route handler is
   thin.

### Error handling

- No candidates → `{ "questions": [] }` (200). Empty bank is normal for a fresh
  legacy.
- DB error → 500 via the existing route error middleware.

---

## Piece 2 — tap-to-seed on `/session/start`

Mirrors the existing `theme_id` mechanism
(`orchestrator/steps/apply_theme_unlock.py`).

### Contract change

`session_metadata` gains one optional field on `POST /session/start` and its
`/session/start/stream` twin:

```jsonc
"session_metadata": {
  "question_id": "uuid"   // NEW, optional — the feed question the user tapped
}
```

### Implementation

1. **New step `apply_picked_question`** in `orchestrator/steps/`. Runs before
   `generate_opener` in the session-start pipeline, in **both** starter and
   steady phases (unlike `select_starter_question`, which is starter-only).

2. Behavior when `session_metadata.question_id` is present:
   - Load the question row scoped to `person_id` and `status='active'`.
   - Set `state.selection` to a `SelectionResult` carrying that question's
     `id`, `text`, `source` — bypassing the selectors entirely and any cooldown/
     recency dedup (explicit pick).
   - `build_starter_context` then grounds `anchor_question_text` on it, and the
     existing `append_opener` marks it seeded + asked in Working Memory.
   - Runs *after* `select_starter_question`, so an explicit pick overrides any
     auto-selected starter question.

3. **Graceful degrade** (mirrors `theme_id`): unknown / foreign-person /
   inactive `question_id` → log + ignore, fall through to the normal opener
   (auto-selected in starter, none in steady). The opener must never fail
   because of a bad picked-question id.

4. **No `answered_by` edge** is written on seed. The answer arrives through
   extraction as usual once the contributor talks.

5. **Wiring:** register the step in both the JSON session-start pipeline and the
   streaming pipeline so both paths honor `question_id` (both already share
   `build_starter_context`).

### Prompt nuance

Because this is an *explicit* user pick (not a soft anchor), verify the
starter-opener prompt (`response_generator/prompts.py`) opens *on* the anchor
question rather than merely near it. If the current wording treats the anchor as
optional/soft, add a minimal signal so an explicitly-picked question is opened on
directly. Keep the change minimal — do not restructure the opener prompt.

---

## Docs to update (lockstep, per CLAUDE.md §10)

- **`API.md`** — remove `/questions/...` from §9 "does NOT expose"; document
  `GET /questions/feed`; document `session_metadata.question_id` on
  `/session/start` (+ stream).
- **`NODE_INTEGRATION.md`** — add the feed surface; note the tap→seed flow and
  that Node passes `question_id` on `/session/start`.
- **`CLAUDE.md` §9** — add `GET /questions/feed` and the `question_id` metadata
  field to the contract list.

## Testing

- **Unit:** `rank_candidates` returns full sorted list; feed applies the
  universal-dimension diversity cap and the `limit` clamp; `skip`/`suppress`
  excluded, `defer` boosted.
- **Orchestrator:** `question_id` in `session_metadata` seeds the opener anchor
  and marks seeded+asked in WM, in both starter and steady phase; unknown id
  degrades to the normal opener.
- **Route:** `GET /questions/feed` happy path (ordered, capped) and empty-bank
  path; `/session/start` with a valid and an invalid `question_id`.

## Invariants touched

- #10 (universal-dimension cap) — applied to the feed slate.
- #23 (`question_decisions`) — `skip`/`suppress` honored in the feed;
  `defer` boosted. The explicit tap is exempt from cooldown/recency (a new,
  narrow exemption documented here).
- #9 (`attributes.themes`) — surfaced as `themes` in the feed payload.
