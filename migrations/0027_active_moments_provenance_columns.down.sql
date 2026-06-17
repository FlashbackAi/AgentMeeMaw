-- ============================================================================
-- 0027_active_moments_provenance_columns.down.sql
-- ============================================================================

BEGIN;

-- Restore the original definition (sans provenance columns).
-- CASCADE drops active_themes_with_tier which is recreated below.
DROP VIEW IF EXISTS active_moments CASCADE;

CREATE VIEW active_moments AS
SELECT
    id,
    person_id,
    title,
    narrative,
    time_anchor,
    life_period_estimate,
    sensory_details,
    emotional_tone,
    contributor_perspective,
    status,
    superseded_by,
    narrative_embedding,
    embedding_model,
    embedding_model_version,
    video_url,
    thumbnail_url,
    generation_prompt,
    created_at,
    updated_at
FROM moments
WHERE status = 'active';

CREATE VIEW active_themes_with_tier AS
SELECT
    t.id,
    t.person_id,
    t.kind,
    t.slug,
    t.display_name,
    t.description,
    t.state,
    t.unlocked_at,
    t.thread_id,
    t.image_url,
    t.thumbnail_url,
    t.created_at,
    t.updated_at,
    COALESCE(stats.qualifying_count, 0)   AS qualifying_count,
    COALESCE(stats.life_period_count, 0)  AS life_period_count,
    COALESCE(stats.has_rich_sensory, false) AS has_rich_sensory,
    CASE
        WHEN t.state = 'locked' THEN NULL
        WHEN COALESCE(stats.qualifying_count, 0) >= 5
         AND COALESCE(stats.life_period_count, 0) >= 3
         AND COALESCE(stats.has_rich_sensory, false) THEN 'testament'
        WHEN COALESCE(stats.qualifying_count, 0) >= 3
          OR COALESCE(stats.life_period_count, 0) >= 2 THEN 'story'
        WHEN COALESCE(stats.qualifying_count, 0) >= 1 THEN 'tale'
        ELSE NULL
    END AS tier,
    CASE
        WHEN COALESCE(stats.qualifying_count, 0) = 0 THEN 'empty'
        WHEN COALESCE(stats.qualifying_count, 0) >= 5
         AND COALESCE(stats.life_period_count, 0) >= 3
         AND COALESCE(stats.has_rich_sensory, false) THEN 'rich'
        WHEN COALESCE(stats.qualifying_count, 0) >= 3
          OR COALESCE(stats.life_period_count, 0) >= 2 THEN 'eligible'
        ELSE 'seeded'
    END AS eligibility,
    CASE
        WHEN t.archetype_answers_draft IS NULL THEN NULL
        ELSE jsonb_build_object(
            'answered',
            (
                SELECT COUNT(*)
                  FROM jsonb_array_elements(t.archetype_answers_draft) a
                 WHERE COALESCE(a->>'option_id', '') <> ''
                    OR COALESCE(a->>'free_text', '') <> ''
                    OR (a->>'skipped')::boolean IS TRUE
            ),
            'total',
            COALESCE(jsonb_array_length(t.archetype_questions), 0)
        )
    END AS archetype_progress
FROM themes t
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) FILTER (
            WHERE m.sensory_details IS NOT NULL
               OR m.time_anchor IS NOT NULL
               OR EXISTS (
                   SELECT 1 FROM edges ie
                    WHERE ie.from_kind = 'moment'
                      AND ie.from_id = m.id
                      AND ie.edge_type = 'involves'
                      AND ie.status = 'active'
               )
        ) AS qualifying_count,
        COUNT(DISTINCT m.life_period_estimate) FILTER (
            WHERE m.life_period_estimate IS NOT NULL
              AND m.life_period_estimate <> ''
        ) AS life_period_count,
        bool_or(
            m.sensory_details IS NOT NULL
            AND char_length(m.sensory_details) > 80
        ) AS has_rich_sensory
      FROM edges e
      JOIN active_moments m ON m.id = e.from_id
     WHERE e.from_kind = 'moment'
       AND e.to_kind   = 'theme'
       AND e.to_id     = t.id
       AND e.edge_type = 'themed_as'
       AND e.status    = 'active'
       AND m.person_id = t.person_id
) stats ON true
WHERE t.status = 'active';

COMMIT;
