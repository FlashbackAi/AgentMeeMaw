-- ============================================================================
-- 0027_active_moments_provenance_columns.up.sql
-- Collaborator Phase 1, sub-project 2: expose provenance columns in the
-- active_moments view so the retrieval layer can SELECT and ORDER BY them.
-- ----------------------------------------------------------------------------
-- active_moments was created in 0001 as SELECT * FROM moments … but Postgres
-- resolves * at view-creation time, so the two columns added in 0026
-- (told_by_user_id, told_by_display_name) did not appear in the view.
-- This migration recreates the view with an explicit column list that
-- includes the new provenance columns.
-- ============================================================================

BEGIN;

-- CASCADE drops dependent views (active_themes_with_tier); they are
-- recreated below with the same definition as in 0022.
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
    told_by_user_id,
    told_by_display_name,
    created_at,
    updated_at
FROM moments
WHERE status = 'active';

-- Recreate active_themes_with_tier, which was dropped by CASCADE above.
-- Definition is identical to 0022; active_moments now supplies the two
-- new provenance columns but active_themes_with_tier does not need them.
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
