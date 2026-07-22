-- ============================================================================
-- 0048_tribute_two_meter.up.sql
-- Flashback AI: Legacy Mode  -  two-meter tribute model
-- ----------------------------------------------------------------------------
-- Splits the single meter into two kinds of tribute row (design 2026-07-22):
--
--   * STANDALONE (campaign_id IS NULL) -- the always-on keepsake. No message
--     slot. Smooth, memories-led percent (graded by depth toward ~5 rich
--     stories). Unlocks on the story floor alone; appearance/signature are
--     soft polish that add % but never gate.
--   * CAMPAIGN (campaign_id set) -- the occasion (Father's Day, Friendship
--     Day). Keeps the four weighted slots (memories 40 / message 30 /
--     appearance 20 / signature 10). Appearance/signature are soft by default
--     but a campaign can flip them into its hard gate via the new
--     require_appearance / require_signature config columns.
--
-- KEY CHANGE: `ready` (the unlock/generate gate) is DECOUPLED from `percent`.
-- A video unlocks at its HARD GATE (stories, + message on campaigns, + any
-- required soft slots); the bar keeps climbing to 100% as soft slots fill.
-- `/generate` now gates on `ready`, not `percent = 100`.
--
-- MEM_TARGET (5.0 depth-weighted) and the story floor (3) are tunable here;
-- checklist.py mirrors them as documentation only.
-- ============================================================================

BEGIN;

ALTER TABLE tribute_campaigns
    ADD COLUMN IF NOT EXISTS require_appearance BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS require_signature  BOOLEAN NOT NULL DEFAULT false;

DROP VIEW IF EXISTS tribute_status;

CREATE VIEW tribute_status AS
SELECT
    tr.id,
    tr.person_id,
    tr.theme_id,
    tr.campaign_id,
    tc.slug                                                 AS campaign_slug,
    tc.display_name                                         AS campaign_display_name,
    (CASE WHEN tr.campaign_id IS NULL THEN 'standalone'
          ELSE 'campaign' END)                              AS meter_kind,
    tr.status,
    COALESCE(mem.qualifying_count, 0)                       AS memories_count,
    (tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0)             AS message_present,
    COALESCE(appr.appearance_present, false)                AS appearance_present,
    COALESCE(sig.signature_present, false)                  AS signature_present,
    COALESCE(ans.answered_layers, 0)                        AS answered_layers,
    -- percent: smooth 0-100. Standalone is memories-led (no message);
    -- campaign keeps the four weighted slots.
    (CASE WHEN tr.campaign_id IS NULL THEN
        round(
            LEAST(COALESCE(mem.moment_score, 0), 5.0) / 5.0 * 70
          + (CASE WHEN COALESCE(appr.appearance_present, false) THEN 20 ELSE 0 END)
          + (CASE WHEN COALESCE(sig.signature_present, false) THEN 10 ELSE 0 END)
        )
     ELSE
        round(
            GREATEST(
                LEAST(COALESCE(ans.answered_layers, 0)::numeric / 14, 0.4) * 40,
                LEAST(COALESCE(mem.moment_score, 0), 3.0) / 3 * 40
            )
          + (CASE WHEN tr.message_text IS NOT NULL
                    AND length(btrim(tr.message_text)) > 0 THEN 30 ELSE 0 END)
          + (CASE WHEN COALESCE(appr.appearance_present, false) THEN 20 ELSE 0 END)
          + (CASE WHEN COALESCE(sig.signature_present, false) THEN 10 ELSE 0 END)
        )
     END)::int                                              AS percent,
    -- ready: the hard gate (unlock/generate). Soft slots gate only when a
    -- campaign requires them; standalone never requires them.
    (CASE WHEN tr.campaign_id IS NULL THEN
        (COALESCE(mem.qualifying_count, 0) >= 3)
     ELSE
        (COALESCE(mem.qualifying_count, 0) >= 3
         AND tr.message_text IS NOT NULL
         AND length(btrim(tr.message_text)) > 0
         AND (NOT COALESCE(tc.require_appearance, false)
              OR COALESCE(appr.appearance_present, false))
         AND (NOT COALESCE(tc.require_signature, false)
              OR COALESCE(sig.signature_present, false)))
     END)                                                   AS ready,
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
    -- answered_layers prefers the tribute row's own answers (per-campaign,
    -- migration 0042), falling back to the theme row for pre-0042 tributes;
    -- counts single (option_label/free_text) and multi-select (option_labels).
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
