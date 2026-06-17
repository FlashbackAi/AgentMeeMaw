-- ============================================================================
-- 0028_collaborator_onboarding.down.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_collaborator_onboarding;
DROP TABLE IF EXISTS collaborator_onboarding;

COMMIT;
