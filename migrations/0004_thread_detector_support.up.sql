-- ============================================================================
-- 0004_thread_detector_support
-- The threads table and edges already exist from migration 0001, and the
-- trigger column persons.moments_at_last_thread_run is also from 0001.
-- This migration is shipped for build-order consistency and as a placeholder
-- for any helper indexes/columns the Thread Detector turns out to need.
-- ============================================================================

BEGIN;

-- No schema changes required for v1. Empty migration committed for
-- numbering / rollback symmetry.

COMMIT;
