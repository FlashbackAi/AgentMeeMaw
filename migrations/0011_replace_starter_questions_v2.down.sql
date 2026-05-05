-- ============================================================================
-- 0011_replace_starter_questions_v2.down.sql
-- Reverse 0011 by rolling back to the 0008 set.
-- ----------------------------------------------------------------------------
-- Drops the v2 starter_anchor templates. To restore 0008's set, re-run
-- 0008_replace_starter_questions.up.sql by hand. We do NOT inline the
-- 0008 INSERTs here, to avoid drift if 0008 ever changes.
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
