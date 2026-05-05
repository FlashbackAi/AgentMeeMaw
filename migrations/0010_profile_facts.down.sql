-- ============================================================================
-- 0010_profile_facts.down.sql  -  Reverse 0010_profile_facts.up.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_profile_facts;
DROP TABLE IF EXISTS profile_facts;

COMMIT;
