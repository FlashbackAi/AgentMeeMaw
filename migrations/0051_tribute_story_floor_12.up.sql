-- ============================================================================
-- 0051_tribute_story_floor_12.up.sql
-- Flashback AI: Legacy Mode  -  raise the tribute story floor to 12
-- ----------------------------------------------------------------------------
-- The 3-story floor let a tribute unlock (and render) almost immediately: a
-- contributor's first session usually clears 3 qualifying moments, so videos
-- went out thin. The floor rises to 12 -- both the `ready` gate and the
-- memories slot's FILLED threshold (checklist.py mirrors it as
-- MEMORIES_TARGET).
--
-- The memories percent term becomes COUNT-based (was depth-weighted
-- moment_score, 0030). With a count gate of 12 a depth-weighted meter lies:
-- rich moments score up to 2.0 each, so ~6-8 rich stories max the bar while
-- `/generate` still 409s -- the same "meter full, no way forward" deadlock
-- class 0050 retired the appearance slot for. Count-in / count-out keeps the
-- bar, the "N of 12 stories" copy, and the gate in lockstep; at 12 steps the
-- bar is smooth enough without depth grading. The archetype answer-floor
-- (0030) is unchanged -- it still credits captured intent on campaign rows
-- and still cannot flip `ready`.
--
-- Weights are unchanged (campaign 50/35/15, standalone 85/15), so the
-- message-invitation floor of 65 keeps meaning "everything except the
-- message is done" (select_message_invitation.py).
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
    -- percent: smooth 0-100. Memories are COUNT-based against the floor of 12
    -- (0051) so the bar can never max before the gate opens. Standalone is
    -- memories-led (no message); campaign keeps memories + message + signature.
    (CASE WHEN tr.campaign_id IS NULL THEN
        round(
            LEAST(COALESCE(mem.qualifying_count, 0), 12)::numeric / 12 * 85
          + (CASE WHEN COALESCE(sig.signature_present, false) THEN 15 ELSE 0 END)
        )
     ELSE
        round(
            GREATEST(
                LEAST(COALESCE(ans.answered_layers, 0)::numeric / 14, 0.4) * 50,
                LEAST(COALESCE(mem.qualifying_count, 0), 12)::numeric / 12 * 50
            )
          + (CASE WHEN tr.message_text IS NOT NULL
                    AND length(btrim(tr.message_text)) > 0 THEN 35 ELSE 0 END)
          + (CASE WHEN COALESCE(sig.signature_present, false) THEN 15 ELSE 0 END)
        )
     END)::int                                              AS percent,
    -- ready: the hard gate (unlock/generate). Story floor is 12 (0051). The
    -- require_appearance/require_signature clauses stay (no-op for all current
    -- campaigns, which have both false); appearance is soft-and-unscored, never
    -- a gate unless a campaign explicitly opts in.
    (CASE WHEN tr.campaign_id IS NULL THEN
        (COALESCE(mem.qualifying_count, 0) >= 12)
     ELSE
        (COALESCE(mem.qualifying_count, 0) >= 12
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
    -- moment_score (depth weighting, 0030) is retired with the count-based
    -- percent; only the raw qualifying count feeds the meter and the gate.
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
    -- appearance_present is still computed + surfaced (Node reads it, and it
    -- signals whether the art has physical ground truth), it is just not
    -- part of `percent` (0050).
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
