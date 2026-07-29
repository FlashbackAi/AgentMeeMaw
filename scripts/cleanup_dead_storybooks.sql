-- Clear the storybooks stranded at status='generating' by the pre-collection
-- render pipeline (prod 2026-07-29).
--
--   psql "$DATABASE_URL" -f scripts/cleanup_dead_storybooks.sql
--
-- WHAT THESE ROWS ARE
--   Five books created 2026-06-17..2026-06-20, all with `collection IS NULL`
--   (they predate the six fixed collections, migration 0036) and no pdf_url.
--   They belong to the retired HTML/Node render path, so no worker will ever
--   pick them up: they have been showing a spinning card on 4 legacies
--   (Rimsha, Chandraiah, chandu, Jankuta Surender x2) for ~40 days.
--
--   Every book minted since 2026-07-03 completed (59/59), so this is historical
--   debris, not a live failure.
--
-- WHY 'failed' RATHER THAN A DELETE
--   'failed' is a state the UI and the user can act on (retry), and it keeps
--   the row for accounting. Deleting would silently drop the evidence.
--
-- THE CODE-SIDE CAUSE IS FIXED SEPARATELY
--   The route used to mint the row, fail to enqueue, and answer 200 with
--   `enqueued: false`. It now marks the row 'failed' and returns 503, so this
--   cleanup should never be needed again. See flashback/storybook/generation.py
--   (StorybookRenderUnavailable) and routes/storybooks.py.
--
-- SCOPE GUARD
--   The predicate is deliberately narrow: status='generating' AND collection IS
--   NULL AND pdf_url IS NULL. A live book always carries a collection, so an
--   in-flight render can never match.

BEGIN;

\echo '=== rows about to be marked failed (expect exactly 5) ==='
SELECT s.id, p.name, s.created_at::date AS created, s.status,
       (now() - s.created_at)::interval(0) AS stuck_for
FROM storybooks s
JOIN persons p ON p.id = s.person_id
WHERE s.status = 'generating'
  AND s.collection IS NULL
  AND s.pdf_url IS NULL
ORDER BY s.created_at;

UPDATE storybooks
   SET status = 'failed'
 WHERE status = 'generating'
   AND collection IS NULL
   AND pdf_url IS NULL;

\echo '=== verify: no stale generating books remain (want 0) ==='
SELECT count(*) AS stale_generating
FROM storybooks
WHERE status = 'generating'
  AND created_at < now() - interval '1 day';

\echo '=== storybooks by status ==='
SELECT status, count(*) FROM storybooks GROUP BY status ORDER BY status;

-- Inspect the output above, then:
COMMIT;
-- ...or ROLLBACK; if anything looks wrong.
