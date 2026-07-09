-- ============================================================================
-- 0038_extraction_outbox_failed_status.down.sql
-- ============================================================================

BEGIN;

-- Re-open any dead jobs so the original CHECK can be restored.
UPDATE extraction_outbox SET status = 'pending' WHERE status = 'failed';

ALTER TABLE extraction_outbox
    DROP CONSTRAINT extraction_outbox_status_check;

ALTER TABLE extraction_outbox
    ADD CONSTRAINT extraction_outbox_status_check
    CHECK (status IN ('pending', 'in_progress', 'sent'));

COMMIT;
