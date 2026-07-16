-- 0041 down: drop campaign relationship targeting.
BEGIN;

ALTER TABLE tribute_campaigns DROP COLUMN IF EXISTS relationship_groups;

COMMIT;
