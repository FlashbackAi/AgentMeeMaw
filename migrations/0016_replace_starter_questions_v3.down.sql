-- ============================================================================
-- 0016_replace_starter_questions_v3.down.sql
-- Reverse 0016 by rolling back to the 0011 set.
-- ----------------------------------------------------------------------------
-- Drops the v3 starter_anchor templates. To restore 0011's set, re-run
-- 0011_replace_starter_questions_v2.up.sql by hand. We do NOT inline the
-- 0011 INSERTs here, to avoid drift if 0011 ever changes.
-- ============================================================================

BEGIN;

DELETE FROM edges
WHERE (from_kind = 'question' AND from_id IN (
        SELECT id FROM questions
        WHERE source = 'starter_anchor' AND person_id IS NULL
      ))
   OR (to_kind = 'question' AND to_id IN (
        SELECT id FROM questions
        WHERE source = 'starter_anchor' AND person_id IS NULL
      ));

DELETE FROM questions
WHERE source = 'starter_anchor'
  AND person_id IS NULL;

COMMIT;
