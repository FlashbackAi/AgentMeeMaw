-- ============================================================================
-- 0024_identity_merge_auto_merge.down.sql
-- ----------------------------------------------------------------------------
-- Reverses 0024. Any rows in the new statuses are first folded back into the
-- pre-0024 status set so the restored CHECK constraint holds:
--   auto_merged -> approved (the merge is real and applied)
--   unmerged    -> rejected (the merge was reversed)
-- ============================================================================

BEGIN;

UPDATE identity_merge_suggestions
   SET status = 'approved',
       approved_at = COALESCE(approved_at, auto_merged_at, now())
 WHERE status = 'auto_merged';

UPDATE identity_merge_suggestions
   SET status = 'rejected',
       rejected_at = COALESCE(rejected_at, unmerged_at, now())
 WHERE status = 'unmerged';

DROP INDEX IF EXISTS identity_merge_suggestions_auto_merged_feed_idx;

ALTER TABLE identity_merge_suggestions
    DROP CONSTRAINT IF EXISTS identity_merge_suggestions_unmerged_at_check;
ALTER TABLE identity_merge_suggestions
    DROP CONSTRAINT IF EXISTS identity_merge_suggestions_auto_merged_at_check;

ALTER TABLE identity_merge_suggestions
    DROP COLUMN IF EXISTS unmerged_at,
    DROP COLUMN IF EXISTS auto_merged_at,
    DROP COLUMN IF EXISTS notification_text,
    DROP COLUMN IF EXISTS undo_snapshot,
    DROP COLUMN IF EXISTS acknowledged,
    DROP COLUMN IF EXISTS confidence;

ALTER TABLE identity_merge_suggestions
    DROP CONSTRAINT identity_merge_suggestions_status_check;

ALTER TABLE identity_merge_suggestions
    ADD CONSTRAINT identity_merge_suggestions_status_check
    CHECK (status IN ('pending', 'approved', 'rejected'));

COMMIT;
