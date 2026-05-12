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

SELECT_UNANSWERED_STARTER = """
SELECT q.id, q.text
FROM active_questions q
WHERE q.source = 'starter_anchor'
  AND q.attributes->>'dimension' = %(dimension)s
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

SELECT_ANY_STARTER_FOR_DIMENSION = """
SELECT q.id, q.text
FROM active_questions q
WHERE q.source = 'starter_anchor'
  AND q.attributes->>'dimension' = %(dimension)s
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
SELECT q.id, q.text, q.source, q.attributes, q.created_at
FROM active_questions q
WHERE q.person_id = %(person_id)s
  AND q.source <> 'starter_anchor'
  AND NOT (q.id = ANY(%(recent_ids)s::uuid[]))
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
