-- ============================================================================
-- 0030_tribute_meter_answer_floor.up.sql
-- Flashback AI: Legacy Mode  -  Smarter tribute completion meter
-- ----------------------------------------------------------------------------
-- The original tribute_status (0027) scored the 40-point "memories" slot as a
-- flat LEAST(qualifying_count, 3) / 3 * 40 -- pure story COUNT. Two problems:
--
--   1. The rich archetype answers a contributor gives at unlock (e.g. "he sold
--      a home", "he lost his mother young") carried ZERO weight, so the meter
--      sat at 0% until extraction caught up -- losing real captured intent.
--   2. A one-line moment counted the same as a vivid, time-anchored, sensory
--      one. Depth was invisible.
--
-- This migration reworks ONLY the memories slot:
--
--   answer_floor  = LEAST(answered_layers / 14, 0.4) * 40   -- caps at 16/40
--   moment_score  = Σ per qualifying moment [ 1.0
--                     + 0.5 if length(sensory_details) > 80
--                     + 0.5 if time_anchor has a 'year' ]
--   memories_pts  = GREATEST(answer_floor, LEAST(moment_score, 3.0)/3 * 40)
--
-- The answer floor lifts the DISPLAYED percent off zero so it reflects intent;
-- the depth weighting rewards vivid stories over bare ones. Crucially, `ready`
-- is UNCHANGED -- it still gates on raw qualifying_count >= 3 (+ the other
-- slots), so archetype answers can move the meter but can NEVER alone make a
-- tribute "ready". The answer is a lead, not a fact (design 2026-06-19).
--
-- answered_layers counts committed archetype answers on the linked theme that
-- carry an actual choice (option_label or free_text) -- pure skips don't count.
-- Exposed as a new column for meter copy ("4 of 14 prompts answered").
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
    COALESCE(ans.answered_layers, 0)                        AS answered_layers,
    (
        GREATEST(
            -- answer floor: captured intent, capped at 16/40
            LEAST(COALESCE(ans.answered_layers, 0)::numeric / 14, 0.4) * 40,
            -- depth-weighted real stories, full at score 3.0
            LEAST(COALESCE(mem.moment_score, 0), 3.0) / 3 * 40
        )
      + (CASE WHEN tr.message_text IS NOT NULL
                AND length(btrim(tr.message_text)) > 0 THEN 30 ELSE 0 END)
      + (CASE WHEN COALESCE(appr.appearance_present, false) THEN 20 ELSE 0 END)
      + (CASE WHEN COALESCE(sig.signature_present, false) THEN 10 ELSE 0 END)
    )::int                                                  AS percent,
    (
        -- READY stays honest: real moments required; the floor can't satisfy it.
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
    -- Committed archetype answers on the linked theme that carry an actual
    -- choice. Pure skips (no option_label and no free_text) don't count.
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
