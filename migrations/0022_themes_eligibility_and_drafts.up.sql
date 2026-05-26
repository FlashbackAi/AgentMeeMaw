-- ============================================================================
-- 0022_themes_eligibility_and_drafts.up.sql
-- Flashback AI: Legacy Mode  -  Themes UX hardening
-- ----------------------------------------------------------------------------
-- Three changes:
--
--   1. ``themes.archetype_answers_draft JSONB`` — partial archetype
--      answers persisted mid-flow so the user can resume from where
--      they left off. Kept distinct from ``archetype_answers`` (which
--      remains the committed-on-unlock prior).
--
--   2. ``active_themes_with_tier`` rebuilt to:
--      * drop ``archetype_ready`` (internal cache state, not user-facing)
--      * add ``eligibility`` (empty | seeded | eligible | rich) so the
--        frontend can vary lock-card affordance without re-deriving
--        thresholds. Computed from the same stats as ``tier`` but
--        independent of ``state``.
--      * add ``archetype_progress`` ({answered, total} | NULL) derived
--        from ``archetype_answers_draft`` so the legacy grid can show
--        a "2/4 answered" badge on still-locked cards.
--
--   3. No data migration — ``archetype_answers_draft`` defaults to NULL
--      and existing themes have no draft state.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Draft column
-- ----------------------------------------------------------------------------

ALTER TABLE themes
    ADD COLUMN archetype_answers_draft JSONB;

-- ----------------------------------------------------------------------------
-- 2. Rebuild active_themes so repository reads can see the new draft column
-- ----------------------------------------------------------------------------

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
    archetype_answers_draft,
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

-- ----------------------------------------------------------------------------
-- 3. Rebuild active_themes_with_tier
-- ----------------------------------------------------------------------------

DROP VIEW IF EXISTS active_themes_with_tier;

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
