-- ============================================================================
-- 0050_tribute_drop_appearance_slot.up.sql
-- Flashback AI: Legacy Mode  -  retire the appearance slot from the meter
-- ----------------------------------------------------------------------------
-- The appearance slot (20%) was capping every tribute's meter: it fills only
-- when the subject's physical ground truth (attire / distinctive_features /
-- build) is captured, which the contextual GT taps rarely surface. Contributors
-- who talked for hours saw the bar plateau (campaign 50/70, standalone <=80)
-- with no obvious way forward, and -- worse -- the in-chat message invitation
-- was gated on appearance being filled, so a legacy without appearance could
-- never be prompted for its message and never reach `ready` at all.
--
-- Fix: appearance is no longer a SCORED meter slot. It drops out of `percent`
-- and the remaining slots reweight to sum to 100:
--
--   * CAMPAIGN:   memories 50 / message 35 / signature 15   (was 40/30/20/10)
--   * STANDALONE: memories 85 / signature 15                (was 70/./20/10)
--
-- `ready` is UNCHANGED. The `require_appearance` clause stays (it is a no-op:
-- every current campaign has require_appearance=false), and `appearance_present`
-- is still computed and surfaced as a column so Node reads don't break -- the
-- ground-truth appearance fields keep feeding the portrait / scene_subject
-- image composers (ground_truth/render.py). This migration only stops SCORING
-- appearance; it does not remove the data or its use in art.
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
    (CASE WHEN tr.campaign_id IS NULL THEN 'standalone'
          ELSE 'campaign' END)                              AS meter_kind,
    tr.status,
    COALESCE(mem.qualifying_count, 0)                       AS memories_count,
    (tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0)             AS message_present,
    COALESCE(appr.appearance_present, false)                AS appearance_present,
    COALESCE(sig.signature_present, false)                  AS signature_present,
    COALESCE(ans.answered_layers, 0)                        AS answered_layers,
    -- percent: smooth 0-100. Appearance is NO LONGER scored (0050). Standalone
    -- is memories-led (no message); campaign keeps memories + message + signature.
    (CASE WHEN tr.campaign_id IS NULL THEN
        round(
            LEAST(COALESCE(mem.moment_score, 0), 5.0) / 5.0 * 85
          + (CASE WHEN COALESCE(sig.signature_present, false) THEN 15 ELSE 0 END)
        )
     ELSE
        round(
            GREATEST(
                LEAST(COALESCE(ans.answered_layers, 0)::numeric / 14, 0.4) * 50,
                LEAST(COALESCE(mem.moment_score, 0), 3.0) / 3 * 50
            )
          + (CASE WHEN tr.message_text IS NOT NULL
                    AND length(btrim(tr.message_text)) > 0 THEN 35 ELSE 0 END)
          + (CASE WHEN COALESCE(sig.signature_present, false) THEN 15 ELSE 0 END)
        )
     END)::int                                              AS percent,
    -- ready: the hard gate (unlock/generate). UNCHANGED from 0048. The
    -- require_appearance/require_signature clauses stay (no-op for all current
    -- campaigns, which have both false); appearance is soft-and-unscored, never
    -- a gate unless a campaign explicitly opts in.
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
    -- appearance_present is still computed + surfaced (Node reads it, and it
    -- signals whether the art has physical ground truth), it is just no longer
    -- part of `percent`.
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
