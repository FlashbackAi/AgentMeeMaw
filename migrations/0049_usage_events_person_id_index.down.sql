-- ============================================================================
-- 0049_usage_events_person_id_index.down.sql
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS usage_events_person_id_idx;

COMMIT;
