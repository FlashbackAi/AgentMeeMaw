-- Diagnose a double-rendered tribute (prod 2026-07-28).
-- Run on the EC2 box (the RDS is VPC-private, NOT reachable from a laptop):
--   psql "$DATABASE_URL" -f scripts/diagnose_tribute_double_render.sql
--
-- Reported symptom: legacy at 65%, tapped Generate, video rendered, button
-- stayed enabled, tapped again -> a SECOND render, card fell back to the 65%
-- meter and flickered. Expectation below: two renders on ONE tribute row
-- (rendered_at bumped, composed_at newer than the first completion).

\set person_id 'd3f96630-e9b0-4b02-8178-0d31a504118e'
\set tribute_id '3c8abc5e-ef37-4b7b-9c03-e87b70e5215c'

\echo '=== 1. Which id is which? (person / tribute / theme / moment) ==='
SELECT 'person'  AS kind, id::text, name AS label FROM persons
 WHERE id IN (:'person_id', :'tribute_id')
UNION ALL
SELECT 'tribute' AS kind, id::text, status FROM tributes
 WHERE id IN (:'person_id', :'tribute_id')
UNION ALL
SELECT 'theme'   AS kind, id::text, slug FROM themes
 WHERE id IN (:'person_id', :'tribute_id')
UNION ALL
SELECT 'moment'  AS kind, id::text, left(title, 40) FROM moments
 WHERE id IN (:'person_id', :'tribute_id');

\echo '=== 2. Every tribute row on this legacy: lifecycle + render outputs ==='
-- The double-render signature: status='generating' WHILE video_url is already
-- set (the contradictory row the card oscillates on), or rendered_at well after
-- the first completion with a newer composed_at.
SELECT
    tr.id,
    ts.meter_kind,
    ts.campaign_slug,
    tr.status,
    ts.percent,
    ts.ready,
    ts.memories_count,
    ts.message_present,
    ts.signature_present,
    (tr.video_url IS NOT NULL)                                   AS has_video,
    (tr.pdf_url   IS NOT NULL)                                   AS has_pdf,
    tr.render_error,
    tr.latest_generation_context -> 'tribute_video' ->> 'composed_at'
                                                                 AS render_composed_at,
    tr.rendered_at,
    tr.updated_at,
    tr.created_at
FROM tributes tr
JOIN tribute_status ts ON ts.id = tr.id
WHERE tr.person_id = :'person_id'
ORDER BY tr.created_at;

\echo '=== 3. Why 65%? (standalone: moment_score/5*85 + 15 if signature) ==='
-- ready is decoupled from percent since 0048: a standalone tribute is ready at
-- 3 qualifying moments, so 65% + ready=true is BY DESIGN, not a reset.
SELECT
    count(*)                                                     AS qualifying,
    round(SUM(1.0
        + CASE WHEN length(COALESCE(m.sensory_details,'')) > 80 THEN 0.5 ELSE 0 END
        + CASE WHEN m.time_anchor ? 'year'                      THEN 0.5 ELSE 0 END
    ), 2)                                                        AS moment_score,
    -- LEAST() ignores NULLs, so an empty pool would read 85 without COALESCE.
    round(LEAST(COALESCE(SUM(1.0
        + CASE WHEN length(COALESCE(m.sensory_details,'')) > 80 THEN 0.5 ELSE 0 END
        + CASE WHEN m.time_anchor ? 'year'                      THEN 0.5 ELSE 0 END
    ), 0), 5.0) / 5.0 * 85)                                      AS percent_from_stories
FROM active_moments m
WHERE m.person_id = :'person_id'
  AND (m.sensory_details IS NOT NULL
    OR m.time_anchor IS NOT NULL
    OR EXISTS (SELECT 1 FROM edges ie
                WHERE ie.from_kind = 'moment' AND ie.from_id = m.id
                  AND ie.edge_type = 'involves' AND ie.status = 'active'));

\echo '=== 4. Fleet-wide: any other tribute showing the contradictory row? ==='
SELECT id, person_id, status, rendered_at, updated_at
FROM tributes
WHERE status = 'generating' AND video_url IS NOT NULL
ORDER BY updated_at DESC
LIMIT 25;

\echo '=== 5. Fleet-wide: renders stuck generating (dead worker) ==='
SELECT id, person_id, status,
       latest_generation_context -> 'tribute_video' ->> 'composed_at' AS composed_at,
       updated_at
FROM tributes
WHERE status = 'generating'
  AND updated_at < now() - interval '30 minutes'
ORDER BY updated_at DESC
LIMIT 25;
