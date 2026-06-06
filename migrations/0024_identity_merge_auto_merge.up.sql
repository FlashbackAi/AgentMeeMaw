-- ============================================================================
-- 0024_identity_merge_auto_merge.up.sql
-- Flashback AI: Legacy Mode - conservative auto-merge + reversible unmerge.
-- ----------------------------------------------------------------------------
-- The reconcile backstop may now AUTO-MERGE a duplicate entity when the
-- verifier returns same_identity + high confidence, instead of only proposing
-- a pending suggestion. Auto-merges are surfaced to the user (notification)
-- and are fully reversible:
--
--   * status gains 'auto_merged' (the merge was applied silently) and
--     'unmerged' (a prior auto/approved merge was reversed by the user).
--   * confidence captures the verifier confidence that drove the disposition.
--   * acknowledged tracks whether the user has dismissed the notification.
--   * undo_snapshot stores the source entity row + every edge touching it
--     (both repointed and deleted) so unmerge can resurrect the entity exactly.
--   * notification_text is the LLM-authored, user-facing one-liner.
-- ============================================================================

BEGIN;

ALTER TABLE identity_merge_suggestions
    DROP CONSTRAINT identity_merge_suggestions_status_check;

ALTER TABLE identity_merge_suggestions
    ADD CONSTRAINT identity_merge_suggestions_status_check
    CHECK (status IN ('pending', 'approved', 'rejected', 'auto_merged', 'unmerged'));

ALTER TABLE identity_merge_suggestions
    ADD COLUMN confidence TEXT
        CHECK (confidence IN ('low', 'medium', 'high')),
    ADD COLUMN acknowledged BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN undo_snapshot JSONB,
    ADD COLUMN notification_text TEXT,
    ADD COLUMN auto_merged_at TIMESTAMPTZ,
    ADD COLUMN unmerged_at TIMESTAMPTZ;

-- Timestamp guards mirror the existing approved/rejected checks.
ALTER TABLE identity_merge_suggestions
    ADD CONSTRAINT identity_merge_suggestions_auto_merged_at_check
    CHECK (
        (status = 'auto_merged' AND auto_merged_at IS NOT NULL)
        OR (status <> 'auto_merged')
    );

ALTER TABLE identity_merge_suggestions
    ADD CONSTRAINT identity_merge_suggestions_unmerged_at_check
    CHECK (
        (status = 'unmerged' AND unmerged_at IS NOT NULL)
        OR (status <> 'unmerged')
    );

-- Notification feed: unacknowledged auto-merges Node polls for the toast.
CREATE INDEX identity_merge_suggestions_auto_merged_feed_idx
    ON identity_merge_suggestions (person_id, acknowledged, auto_merged_at DESC)
    WHERE status = 'auto_merged';

COMMIT;
