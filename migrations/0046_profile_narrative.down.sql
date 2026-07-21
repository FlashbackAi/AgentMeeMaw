-- 0046_profile_narrative.down.sql  -  reverse 0046

BEGIN;

ALTER TABLE relationship_profiles
    DROP COLUMN IF EXISTS narrative;

COMMIT;
