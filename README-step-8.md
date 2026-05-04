# Step 8 - Phase Gate + Question Selection

This step replaces the random starter-anchor placeholder with deterministic
Phase Gate selection. The new code lives under `src/flashback/phase_gate/`
and stays pure SQL plus Python: no LLM calls, no prompt decisions.

## What It Ships

```
src/flashback/
    phase_gate/
        gate.py                 phase router
        starter_selector.py     coverage-driven starter anchors
        steady_selector.py      source + diversity ranking
        ranking.py              score constants and helpers
        queries.py              SQL constants
        schema.py               SelectionResult + PhaseGateError
    working_memory/
        keys.py                 adds wm:session:{id}:asked
        client.py               recently asked append/read helpers
    orchestrator/
        orchestrator.py         session-start and switch-intent wiring
```

## Selection Paths

`/session/start` always calls `select_starter_question(person_id)`. That
chooses a `starter_anchor` template and passes its text and dimension into
the starter opener context.

`/turn` calls `select_next_question(person_id, session_id)` only when the
Intent Classifier returns `intent='switch'`. The Phase Gate reads
`persons.phase`: starter phase routes back to starter selection; steady
phase ranks the person-owned question bank.

## Starter Rule

The first turn ever for a legacy is always sensory: if the person has no
active moments, the selector forces `dimension='sensory'`.

After moments exist, the selector reads `persons.coverage_state`, chooses
the lowest-count dimension, and breaks ties in this order:

```
sensory > voice > place > relation > era
```

It first filters out starter templates already connected by an
`answered_by` edge to one of the person's active moments. If every template
for that dimension has been answered, it falls back to any active template
for the dimension. If no template exists, `PhaseGateError` is raised.

## Steady Ranking

Steady selection pulls the session's last five seeded question ids from
Working Memory, unions their `attributes.themes`, then scores active
person-owned candidates:

```
combined_score = source_priority_score + 2.0 * diversity_score
diversity_score = 1 - (overlapping_recent_themes / question_themes)
```

Source priority is:

```
dropped_reference > underdeveloped_entity > thread_deepen
  > life_period_gap > universal_dimension
```

Ties are newest first.

## Universal Cap

The docs frame the cap as "no more than 1 universal_dimension in the top
5." Step 8 only returns one question, so the implementation uses the
single-pick interpretation from the prompt: if the top candidate is
`universal_dimension` and a non-universal candidate is within 1.5 score
points, prefer the non-universal candidate.

## Error Matrix

- `/session/start`: Phase Gate failure returns HTTP 503. Missing starter
  templates are deployment failures and should be loud.
- `/turn` with `intent='switch'`: Phase Gate failure degrades gracefully.
  The Response Generator runs with `seeded_question_text=None`.
- Steady empty bank: valid selection result with no question fields; the
  Response Generator still handles the switch response.

## Working Memory

Recently asked questions are stored in a Valkey LIST:

```
wm:session:{session_id}:asked
```

`append_asked_question()` uses `RPUSH`, trims to the last five entries, and
refreshes TTL. `get_recently_asked_question_ids()` returns oldest first.

## Verified

- [x] Starter selector covers first-turn sensory, coverage tiebreaks,
      answered-template filtering, fallback, and missing-seed errors.
- [x] Steady selector covers empty banks, source priority, diversity,
      universal demotion, recent exclusion SQL, and recency tiebreaks.
- [x] Phase Gate router dispatches by `persons.phase`.
- [x] Working Memory tracks last-five asked ids and refreshes TTL.
- [x] Orchestrator fires Phase Gate only at `/session/start` and
      `intent='switch'`.
- [x] Full local suite:
      `python -m pytest -q` -> **202 passed** with `TEST_DATABASE_URL`
      loaded from `.env.local`.

## Deviations

- **Package layout:** implemented under `src/flashback/phase_gate/...`,
  matching the existing package layout rather than prompt shorthand
  `src/phase_gate/...`.
- **Session-start WM ownership:** this repo's step-7 HTTP route still
  initializes Working Memory after `handle_session_start`. To avoid
  creating a partial state hash before initialization, `/session/start`
  records `last_seeded_question_id` and appends the asked id in the route.
  `/turn` records switch selections inside the orchestrator because WM
  already exists there.
- **`SelectionResult.dimension`:** kept constrained to the five starter
  dimensions. `universal_dimension.attributes.dimension` can be broader
  (`childhood`, `work`, etc.), so steady universal selections leave this
  typed field as `None`.
- **Query tests:** the lightweight query tests assert the SQL contract
  structurally; the broader DB-backed suite now runs when
  `TEST_DATABASE_URL` is loaded from `.env.local`.
