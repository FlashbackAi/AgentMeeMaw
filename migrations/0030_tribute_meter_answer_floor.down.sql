-- ============================================================================
-- 0030_tribute_meter_answer_floor.down.sql
-- Restore the original flat-count tribute_status view from 0027.
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS tribute_status;

CREATE VIEW tribute_status AS
SELECT
    tr.id,
    tr.person_id,
    tr.theme_id,
    tr.status,
    COALESCE(mem.qualifying_count, 0)                       AS memories_count,
    (tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0)             AS message_present,
    COALESCE(appr.appearance_present, false)                AS appearance_present,
    COALESCE(sig.signature_present, false)                  AS signature_present,
    (
        (LEAST(COALESCE(mem.qualifying_count, 0), 3)::numeric / 3 * 40)
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
    tr.image_url,
    tr.thumbnail_url,
    tr.created_at,
    tr.updated_at
FROM tributes tr
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS qualifying_count
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
WHERE tr.status <> 'superseded';

COMMIT;
