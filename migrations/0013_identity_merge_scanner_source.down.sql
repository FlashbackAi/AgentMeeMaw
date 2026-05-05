-- ============================================================================
-- 0013_identity_merge_scanner_source.down.sql
-- Revert scanner source support.
-- ============================================================================

BEGIN;

UPDATE identity_merge_suggestions
   SET source = 'admin'
 WHERE source = 'scanner';

ALTER TABLE identity_merge_suggestions
    DROP CONSTRAINT identity_merge_suggestions_source_check;

ALTER TABLE identity_merge_suggestions
    ADD CONSTRAINT identity_merge_suggestions_source_check
    CHECK (source IN ('extraction', 'user_edit', 'admin'));

COMMIT;
