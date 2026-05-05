-- ============================================================================
-- 0013_identity_merge_scanner_source.up.sql
-- Allow background identity analysis to create merge suggestions.
-- ============================================================================

BEGIN;

ALTER TABLE identity_merge_suggestions
    DROP CONSTRAINT identity_merge_suggestions_source_check;

ALTER TABLE identity_merge_suggestions
    ADD CONSTRAINT identity_merge_suggestions_source_check
    CHECK (source IN ('extraction', 'scanner', 'user_edit', 'admin'));

COMMIT;
