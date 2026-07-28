-- Clean up tribute rows stranded by the keepsake -> campaign conversion
-- (prod 2026-07-28: "flashback made but the progress is stuck at 65%").
--
--   psql "$DATABASE_URL" -f scripts/cleanup_stranded_tributes.sql
--
-- Two narrow rules. Both were dry-run verified against prod on 2026-07-28 and
-- matched exactly 5 rows (Bot, Padma, Srinidhi / Hemanth, Rimsha). Legacies
-- with no finished sibling (Chitti, Shivani, Sowjanya) are LEFT ALONE -- those
-- are live in-progress tributes, not duplicates.
--
-- Rule A -- UN-STAMP a converted keepsake.
--   A keepsake (campaign_id NULL) has no message slot, so it was legitimately
--   ready and rendered. It then got stamped into a campaign row, which added a
--   message slot it had never been asked to fill: 50 (stories) + 0 (message)
--   + 15 (signature) = 65%, ready=false, with a finished video on it.
--   Restoring campaign_id = NULL restores the meter it actually satisfied. The
--   video/PDF are untouched, and its render snapshot still pins the campaign
--   skin, so a later regenerate keeps the same look.
--
-- Rule B -- SUPERSEDE a stale duplicate card.
--   A message-less campaign row with NO video, on a legacy that already has a
--   completed campaign row WITH a video for the same slug. This is the second
--   card in the gallery (the one showing 65% next to the finished one).

BEGIN;

\echo '=== A. rows to un-stamp (converted keepsake, finished video, no message) ==='
SELECT tr.id, p.name, tr.campaign_id, ts.percent, tr.rendered_at
FROM tributes tr
JOIN persons p ON p.id = tr.person_id
JOIN tribute_status ts ON ts.id = tr.id
WHERE tr.status = 'complete'
  AND tr.video_url IS NOT NULL
  AND tr.campaign_id IS NOT NULL
  AND (tr.message_text IS NULL OR length(btrim(tr.message_text)) = 0)
ORDER BY p.name;

UPDATE tributes tr
   SET campaign_id = NULL
 WHERE tr.status = 'complete'
   AND tr.video_url IS NOT NULL
   AND tr.campaign_id IS NOT NULL
   AND (tr.message_text IS NULL OR length(btrim(tr.message_text)) = 0);

\echo '=== B. rows to supersede (stale duplicate card, no video, done sibling) ==='
SELECT tr.id, p.name, tr.status, ts.percent, tr.created_at
FROM tributes tr
JOIN persons p ON p.id = tr.person_id
JOIN tribute_status ts ON ts.id = tr.id
JOIN tribute_campaigns tc ON tc.id = tr.campaign_id
WHERE tr.status <> 'superseded'
  AND tr.video_url IS NULL
  AND (tr.message_text IS NULL OR length(btrim(tr.message_text)) = 0)
  AND EXISTS (
      SELECT 1 FROM tributes sib
        JOIN tribute_campaigns stc ON stc.id = sib.campaign_id
       WHERE sib.person_id = tr.person_id
         AND sib.id <> tr.id
         AND stc.slug = tc.slug
         AND sib.status = 'complete'
         AND sib.video_url IS NOT NULL)
ORDER BY p.name;

UPDATE tributes tr
   SET status = 'superseded'
 WHERE tr.status <> 'superseded'
   AND tr.video_url IS NULL
   AND (tr.message_text IS NULL OR length(btrim(tr.message_text)) = 0)
   AND tr.campaign_id IS NOT NULL
   AND EXISTS (
       SELECT 1 FROM tributes sib
         JOIN tribute_campaigns stc ON stc.id = sib.campaign_id
         JOIN tribute_campaigns tc  ON tc.id  = tr.campaign_id
        WHERE sib.person_id = tr.person_id
          AND sib.id <> tr.id
          AND stc.slug = tc.slug
          AND sib.status = 'complete'
          AND sib.video_url IS NOT NULL);

\echo '=== verify: finished videos still reading 65% (want 0) ==='
SELECT count(*) AS stuck_with_video
FROM tribute_status ts
JOIN tributes tr ON tr.id = ts.id
WHERE ts.meter_kind = 'campaign' AND ts.percent = 65
  AND tr.status <> 'superseded' AND tr.video_url IS NOT NULL;

\echo '=== verify: one card per legacy per occasion ==='
SELECT p.name, tc.slug, count(*) AS live_rows
FROM tributes tr
JOIN persons p ON p.id = tr.person_id
LEFT JOIN tribute_campaigns tc ON tc.id = tr.campaign_id
WHERE tr.status <> 'superseded'
GROUP BY p.name, tc.slug
HAVING count(*) > 1
ORDER BY count(*) DESC;

-- Inspect the output above, then:
COMMIT;
-- ...or ROLLBACK; if anything looks wrong.
