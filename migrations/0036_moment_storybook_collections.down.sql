BEGIN;

-- The up migration did not touch any view (the repository reads the base
-- table), so the column has no view dependents and drops cleanly.
DROP INDEX IF EXISTS idx_moments_storybook_collections;

ALTER TABLE moments DROP COLUMN IF EXISTS storybook_collections;

COMMIT;
