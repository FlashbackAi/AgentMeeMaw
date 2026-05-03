-- ============================================================================
-- 0002_seed_starter_questions.down.sql
-- Removes the starter_anchor templates seeded by .up.sql.
--
-- Defensive: also removes any edges referencing those templates so we don't
-- leave dangling rows in the edges table (which has no FK enforcement).
-- ============================================================================

BEGIN;

-- Edges that reference any starter_anchor template (in either direction)
DELETE FROM edges
WHERE (from_kind = 'question' AND from_id IN (
        SELECT id FROM questions
        WHERE source = 'starter_anchor' AND person_id IS NULL
      ))
   OR (to_kind = 'question' AND to_id IN (
        SELECT id FROM questions
        WHERE source = 'starter_anchor' AND person_id IS NULL
      ));

-- The templates themselves
DELETE FROM questions
WHERE source = 'starter_anchor'
  AND person_id IS NULL;

COMMIT;
