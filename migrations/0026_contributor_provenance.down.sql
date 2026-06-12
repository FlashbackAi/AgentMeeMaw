-- ============================================================================
-- 0026_contributor_provenance.down.sql
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS idx_moments_person_told_by_active;

ALTER TABLE processed_extractions DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE profile_facts         DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE questions             DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE traits                DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE entities              DROP COLUMN IF EXISTS told_by_user_id;
ALTER TABLE moments               DROP COLUMN IF EXISTS told_by_display_name;
ALTER TABLE moments               DROP COLUMN IF EXISTS told_by_user_id;

COMMIT;
