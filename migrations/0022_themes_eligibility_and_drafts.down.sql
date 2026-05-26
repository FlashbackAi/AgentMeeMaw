-- ============================================================================
-- 0022_themes_eligibility_and_drafts.down.sql
-- ----------------------------------------------------------------------------
-- Restore the 0020 shape of active_themes_with_tier and drop the draft column.
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_themes_with_tier;
DROP VIEW IF EXISTS active_themes;

CREATE VIEW active_themes AS
SELECT
    id,
    person_id,
    kind,
    slug,
    display_name,
    description,
    state,
    archetype_questions,
    archetype_answers,
    unlocked_at,
    thread_id,
    image_url,
    thumbnail_url,
    generation_prompt,
    status,
    created_at,
    updated_at
FROM themes
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
    (t.archetype_questions IS NOT NULL) AS archetype_ready,
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
    END AS tier
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

ALTER TABLE themes DROP COLUMN IF EXISTS archetype_answers_draft;

COMMIT;
