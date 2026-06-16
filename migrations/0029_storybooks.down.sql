-- ============================================================================
-- 0029_storybooks.down.sql  -  reverse 0029_storybooks.up.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_storybooks;
DROP TABLE IF EXISTS storybooks;  -- drops grants + indexes + trigger with it

ALTER TABLE persons DROP COLUMN IF EXISTS moments_at_last_storybook_run;

COMMIT;
