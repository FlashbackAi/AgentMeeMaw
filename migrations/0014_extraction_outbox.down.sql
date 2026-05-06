-- ============================================================================
-- 0014_extraction_outbox.down.sql
-- Roll back durable extraction fan-out.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS extraction_outbox_source_message_idx;
DROP INDEX IF EXISTS extraction_outbox_stale_in_progress_idx;
DROP INDEX IF EXISTS extraction_outbox_pending_idx;
DROP TABLE IF EXISTS extraction_outbox;

COMMIT;
