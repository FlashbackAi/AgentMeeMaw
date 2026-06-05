"""SQL constants used by the deterministic Phase Gate selectors."""

from __future__ import annotations

READ_PERSON_PHASE = """
SELECT phase
FROM persons
WHERE id = %(person_id)s
"""

READ_PERSON_NAME_AND_GENDER = """
SELECT name, gender
FROM persons
WHERE id = %(person_id)s
"""

READ_COVERAGE_STATE = """
SELECT coverage_state
FROM persons
WHERE id = %(person_id)s
"""

HAS_ACTIVE_MOMENTS = """
SELECT count(*) > 0 AS has_moments
FROM active_moments
WHERE person_id = %(person_id)s
"""

SELECT_UNANSWERED_COVERAGE_TAP = """
SELECT q.id, q.text
FROM active_questions q
WHERE q.source = 'coverage_tap'
  AND q.person_id IS NULL
  AND q.attributes->>'dimension' = %(dimension)s
  AND NOT (q.id = ANY(%(recent_ids)s::uuid[]))
  AND NOT EXISTS (
    SELECT 1
    FROM active_question_decisions d
    WHERE d.question_id = q.id
      AND d.person_id   = %(person_id)s
      AND d.action      = 'suppress'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM active_edges e
    JOIN active_moments m ON m.id = e.to_id
    WHERE e.from_kind = 'question'
      AND e.from_id = q.id
      AND e.edge_type = 'answered_by'
      AND e.to_kind = 'moment'
      AND m.person_id = %(person_id)s
  )
ORDER BY random()
LIMIT 1
"""

SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION = """
SELECT q.id, q.text
FROM active_questions q
WHERE q.source = 'coverage_tap'
  AND q.person_id IS NULL
  AND q.attributes->>'dimension' = %(dimension)s
  AND NOT (q.id = ANY(%(recent_ids)s::uuid[]))
ORDER BY random()
LIMIT 1
"""

SELECT_RECENT_THEMES = """
SELECT COALESCE(array_agg(DISTINCT theme), ARRAY[]::text[]) AS themes
FROM active_questions q
CROSS JOIN LATERAL jsonb_array_elements_text(q.attributes->'themes') AS theme
WHERE q.id = ANY(%(question_ids)s::uuid[])
"""

SELECT_STEADY_CANDIDATES = """
SELECT q.id, q.text, q.source, q.attributes, q.created_at,
       d.action       AS decision_action,
       d.decided_at   AS decision_decided_at
FROM active_questions q
LEFT JOIN active_question_decisions d
  ON d.question_id = q.id
 AND d.person_id   = %(person_id)s
WHERE q.person_id = %(person_id)s
  AND q.source    = ANY(%(sources)s::text[])
  AND NOT (q.id   = ANY(%(recent_ids)s::uuid[]))
  AND (d.action IS NULL OR d.action != 'suppress')
  AND (
        NOT %(exclude_skipped)s
        OR d.action IS NULL
        OR d.action != 'skip'
      )
  -- Suppress generalises to siblings. A producer re-mints a fresh
  -- question row (new id) for the same dropped phrase or the same
  -- targeted entity on every run, so an id-scoped suppress alone would
  -- be defeated the next session. These two clauses exclude every active
  -- question that shares the dropped_phrase, or the targets-entity, of
  -- ANY actively-suppressed question for this person.
  AND NOT EXISTS (
        SELECT 1
        FROM active_question_decisions sd
        JOIN questions sq ON sq.id = sd.question_id
        WHERE sd.person_id = %(person_id)s
          AND sd.action    = 'suppress'
          AND q.attributes->>'dropped_phrase' IS NOT NULL
          AND lower(btrim(sq.attributes->>'dropped_phrase'))
              = lower(btrim(q.attributes->>'dropped_phrase'))
      )
  AND NOT EXISTS (
        SELECT 1
        FROM active_question_decisions sd
        JOIN active_edges se
          ON se.from_kind = 'question'
         AND se.from_id   = sd.question_id
         AND se.edge_type = 'targets'
         AND se.to_kind   = 'entity'
        JOIN active_edges qe
          ON qe.from_kind = 'question'
         AND qe.from_id   = q.id
         AND qe.edge_type = 'targets'
         AND qe.to_kind   = 'entity'
         AND qe.to_id     = se.to_id
        WHERE sd.person_id = %(person_id)s
          AND sd.action    = 'suppress'
      )
  -- life_period_gap, universal_dimension, and thread_deepen producers
  -- ALSO re-mint a fresh row (new id) every run with no dedup, keyed on
  -- attributes.life_period / attributes.dimension / a motivated_by edge
  -- to a thread respectively. The two clauses above only cover
  -- dropped_phrase + targets-entity, so without these three a suppress
  -- on any of those sources is defeated by the next producer run. Each
  -- clause excludes every active question sharing the same life_period,
  -- dimension, or motivated-by thread as ANY actively-suppressed question.
  AND NOT EXISTS (
        SELECT 1
        FROM active_question_decisions sd
        JOIN questions sq ON sq.id = sd.question_id
        WHERE sd.person_id = %(person_id)s
          AND sd.action    = 'suppress'
          AND sq.source    = 'life_period_gap'
          AND q.source     = 'life_period_gap'
          AND q.attributes->>'life_period'  IS NOT NULL
          AND sq.attributes->>'life_period' = q.attributes->>'life_period'
      )
  AND NOT EXISTS (
        SELECT 1
        FROM active_question_decisions sd
        JOIN questions sq ON sq.id = sd.question_id
        WHERE sd.person_id = %(person_id)s
          AND sd.action    = 'suppress'
          AND sq.source    = 'universal_dimension'
          AND q.source     = 'universal_dimension'
          AND q.attributes->>'dimension'  IS NOT NULL
          AND sq.attributes->>'dimension' = q.attributes->>'dimension'
      )
  AND NOT EXISTS (
        SELECT 1
        FROM active_question_decisions sd
        JOIN active_edges se
          ON se.from_kind = 'question'
         AND se.from_id   = sd.question_id
         AND se.edge_type = 'motivated_by'
         AND se.to_kind   = 'thread'
        JOIN active_edges qe
          ON qe.from_kind = 'question'
         AND qe.from_id   = q.id
         AND qe.edge_type = 'motivated_by'
         AND qe.to_kind   = 'thread'
         AND qe.to_id     = se.to_id
        WHERE sd.person_id = %(person_id)s
          AND sd.action    = 'suppress'
      )
ORDER BY
  CASE q.source
    WHEN 'dropped_reference' THEN 0
    WHEN 'underdeveloped_entity' THEN 1
    WHEN 'thread_deepen' THEN 2
    WHEN 'life_period_gap' THEN 3
    WHEN 'universal_dimension' THEN 4
    ELSE 5
  END,
  q.created_at DESC
LIMIT 50
"""
