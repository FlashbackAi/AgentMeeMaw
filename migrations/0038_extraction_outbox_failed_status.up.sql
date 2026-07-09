-- ============================================================================
-- 0038_extraction_outbox_failed_status.up.sql
-- Terminal 'failed' state for poisoned outbox jobs.
--
-- Before this, _mark_failed always reset a job to 'pending' with a backoff
-- capped at 300s — a permanently-poisoned job (unknown job_type, payload the
-- sender rejects) re-attempted every 5 minutes forever with no give-up path.
-- The drain loop only picks 'pending'/'in_progress', so 'failed' rows are
-- inert; they keep last_error for diagnosis and can be manually re-opened by
-- flipping status back to 'pending'.
-- ============================================================================

BEGIN;

ALTER TABLE extraction_outbox
    DROP CONSTRAINT extraction_outbox_status_check;

ALTER TABLE extraction_outbox
    ADD CONSTRAINT extraction_outbox_status_check
    CHECK (status IN ('pending', 'in_progress', 'sent', 'failed'));

COMMIT;
