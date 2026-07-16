-- ============================================================================
-- 0040_tribute_status_campaign.up.sql
-- Flashback AI: Legacy Mode  -  per-campaign tribute videos are labelable
-- ----------------------------------------------------------------------------
-- A person accumulates one tribute row PER CAMPAIGN entry (the FD video, the
-- Friendship Day video, ...). tribute_status had no campaign columns, so
-- Node/FE could neither label the videos nor build the gallery -- the UI
-- showed "one video" and every new campaign render looked like it replaced
-- the previous one (it never did; the rows and URLs all survive).
--
-- Recreates tribute_status with campaign_id + campaign_slug +
-- campaign_display_name (LEFT JOIN tribute_campaigns; NULLs for pre-0039 /
-- neutral tributes). The meter math is UNCHANGED from 0033/0030 -- only the
-- surfaced columns grow.
-- ============================================================================

BEGIN;

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
          CASE WHEN jsonb_typeof(th.archetype_answers) = 'array'
               THEN th.archetype_answers ELSE '[]'::jsonb END
      ) e
     WHERE th.id = tr.theme_id
       AND (
            COALESCE(e ->> 'option_label', '') <> ''
         OR COALESCE(e ->> 'free_text', '') <> ''
       )
) ans ON true
WHERE tr.status <> 'superseded';

COMMIT;
