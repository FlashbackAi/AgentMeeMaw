-- ============================================================================
-- 0042_tribute_archetype_answers.up.sql
-- Flashback AI: Legacy Mode  -  per-campaign archetype answers
-- ----------------------------------------------------------------------------
-- Archetype answers lived only on the (one-per-person) tribute THEME row, so
-- every campaign shared one answer set: a new campaign's meter was pre-filled
-- by last occasion's answers, and committing new answers overwrote them.
--
-- tributes.archetype_answers carries the answers given under THAT campaign's
-- tribute (merged by question_text across re-entries). The meter's
-- answered_layers prefers the tribute row's answers when it has any, falling
-- back to the theme row for pre-0042 tributes. Also counts multi-select
-- answers (option_labels[]), which the old predicate missed.
-- ============================================================================

BEGIN;

ALTER TABLE tributes ADD COLUMN IF NOT EXISTS archetype_answers JSONB;

DROP VIEW IF EXISTS tribute_status;

CREATE VIEW tribute_status AS
SELECT
    tr.id,
    tr.person_id,
    tr.theme_id,
    tr.campaign_id,
    tc.slug                                                 AS campaign_slug,
    tc.display_name                                         AS campaign_display_name,
    tr.status,
    COALESCE(mem.qualifying_count, 0)                       AS memories_count,
    (tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0)             AS message_present,
    COALESCE(appr.appearance_present, false)                AS appearance_present,
    COALESCE(sig.signature_present, false)                  AS signature_present,
    COALESCE(ans.answered_layers, 0)                        AS answered_layers,
    (
        GREATEST(
            LEAST(COALESCE(ans.answered_layers, 0)::numeric / 14, 0.4) * 40,
            LEAST(COALESCE(mem.moment_score, 0), 3.0) / 3 * 40
        )
      + (CASE WHEN tr.message_text IS NOT NULL
                AND length(btrim(tr.message_text)) > 0 THEN 30 ELSE 0 END)
      + (CASE WHEN COALESCE(appr.appearance_present, false) THEN 20 ELSE 0 END)
      + (CASE WHEN COALESCE(sig.signature_present, false) THEN 10 ELSE 0 END)
    )::int                                                  AS percent,
    (
        COALESCE(mem.qualifying_count, 0) >= 3
        AND tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0
        AND COALESCE(appr.appearance_present, false)
        AND COALESCE(sig.signature_present, false)
    )                                                       AS ready,
    tr.video_url,
    tr.pdf_url,
    tr.image_url,
    tr.thumbnail_url,
    tr.rendered_at,
    tr.created_at,
    tr.updated_at
FROM tributes tr
LEFT JOIN tribute_campaigns tc ON tc.id = tr.campaign_id
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) AS qualifying_count,
        COALESCE(SUM(
            1.0
          + CASE WHEN length(COALESCE(m.sensory_details, '')) > 80
                 THEN 0.5 ELSE 0 END
          + CASE WHEN m.time_anchor ? 'year'
                 THEN 0.5 ELSE 0 END
        ), 0) AS moment_score
      FROM active_moments m
     WHERE m.person_id = tr.person_id
       AND (
            m.sensory_details IS NOT NULL
         OR m.time_anchor IS NOT NULL
         OR EXISTS (
             SELECT 1 FROM edges ie
              WHERE ie.from_kind = 'moment'
                AND ie.from_id   = m.id
                AND ie.edge_type = 'involves'
                AND ie.status    = 'active'
         )
       )
) mem ON true
LEFT JOIN LATERAL (
    SELECT (
        (p.ground_truth -> 'region' ->> 'value') IS NOT NULL
        AND (
            (p.ground_truth -> 'birth_era' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'era_span' ->> 'value') IS NOT NULL
        )
        AND (
            (p.ground_truth -> 'distinctive_features' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'attire' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'build' ->> 'value') IS NOT NULL
        )
    ) AS appearance_present
      FROM persons p
     WHERE p.id = tr.person_id
) appr ON true
LEFT JOIN LATERAL (
    SELECT (
        EXISTS (
            SELECT 1 FROM active_traits tt WHERE tt.person_id = tr.person_id
        )
        OR EXISTS (
            SELECT 1 FROM active_entities en
             WHERE en.person_id = tr.person_id
               AND (en.attributes ? 'saying' OR en.attributes ? 'mannerism')
        )
    ) AS signature_present
) sig ON true
LEFT JOIN LATERAL (
    SELECT COUNT(*)::int AS answered_layers
      FROM themes th
      CROSS JOIN LATERAL jsonb_array_elements(
          CASE
            WHEN jsonb_typeof(tr.archetype_answers) = 'array'
                 AND jsonb_array_length(tr.archetype_answers) > 0
            THEN tr.archetype_answers
            WHEN jsonb_typeof(th.archetype_answers) = 'array'
            THEN th.archetype_answers
            ELSE '[]'::jsonb
          END
      ) e
     WHERE th.id = tr.theme_id
       AND (
            COALESCE(e ->> 'option_label', '') <> ''
         OR COALESCE(e ->> 'free_text', '') <> ''
         OR (jsonb_typeof(e -> 'option_labels') = 'array'
             AND jsonb_array_length(e -> 'option_labels') > 0)
       )
) ans ON true
WHERE tr.status <> 'superseded';

COMMIT;
