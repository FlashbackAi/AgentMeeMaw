"""Literal SQL used by the read-only Retrieval Service."""

SEARCH_MOMENTS_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT
        id, person_id, title, narrative, time_anchor,
        life_period_estimate, sensory_details, emotional_tone,
        contributor_perspective, created_at, narrative_embedding
    FROM   active_moments
    WHERE  person_id              = %(person_id)s
      AND  embedding_model         = %(embedding_model)s
      AND  embedding_model_version = %(embedding_model_version)s
      AND  narrative_embedding IS NOT NULL
)
SELECT
    id, person_id, title, narrative, time_anchor,
    life_period_estimate, sensory_details, emotional_tone,
    contributor_perspective, created_at,
    (narrative_embedding <=> %(query_vector)s) AS similarity_score
FROM   candidates
ORDER  BY narrative_embedding <=> %(query_vector)s
LIMIT  %(limit)s
"""

GET_ENTITIES_SQL = """
SELECT id, person_id, kind, name, description, aliases, attributes, created_at
FROM   active_entities
WHERE  person_id = %(person_id)s
ORDER  BY created_at DESC
"""

GET_ENTITIES_BY_KIND_SQL = """
SELECT id, person_id, kind, name, description, aliases, attributes, created_at
FROM   active_entities
WHERE  person_id = %(person_id)s
  AND  kind      = %(kind)s
ORDER  BY created_at DESC
"""

GET_RELATED_MOMENTS_SQL = """
SELECT
    m.id, m.person_id, m.title, m.narrative, m.time_anchor,
    m.life_period_estimate, m.sensory_details, m.emotional_tone,
    m.contributor_perspective, m.created_at,
    NULL::double precision AS similarity_score
FROM   active_entities ent
JOIN   active_edges e
  ON   e.to_kind   = 'entity'
  AND  e.to_id     = ent.id
  AND  e.edge_type = 'involves'
  AND  e.from_kind = 'moment'
JOIN   active_moments m
  ON   m.id        = e.from_id
  AND  m.person_id = ent.person_id
WHERE  ent.id        = %(entity_id)s
  AND  ent.person_id = %(person_id)s
ORDER  BY m.created_at DESC
LIMIT  %(limit)s
"""

GET_THREADS_SQL = """
SELECT id, person_id, name, description, source, confidence, created_at
FROM   active_threads
WHERE  person_id = %(person_id)s
ORDER  BY created_at DESC
"""

GET_THREADS_FOR_ENTITY_SQL = """
SELECT t.id, t.person_id, t.name, t.description, t.source, t.confidence, t.created_at
FROM   active_entities ent
JOIN   active_edges e
  ON   e.from_kind = 'entity'
  AND  e.from_id   = ent.id
  AND  e.edge_type = 'evidences'
  AND  e.to_kind   = 'thread'
JOIN   active_threads t
  ON   t.id        = e.to_id
  AND  t.person_id = ent.person_id
WHERE  ent.id        = %(entity_id)s
  AND  ent.person_id = %(person_id)s
ORDER  BY t.created_at DESC
"""

GET_DROPPED_PHRASES_SQL = """
SELECT
    id AS question_id,
    text,
    attributes->>'dropped_phrase' AS dropped_phrase,
    created_at
FROM   active_questions
WHERE  person_id = %(person_id)s
  AND  source    = 'dropped_reference'
  AND  attributes ? 'dropped_phrase'
ORDER  BY created_at DESC
"""

GET_SESSION_SUMMARY_SQL = """
SELECT NULL::uuid AS session_id, NULL::text AS summary, NULL::timestamptz AS created_at
WHERE  FALSE
"""
