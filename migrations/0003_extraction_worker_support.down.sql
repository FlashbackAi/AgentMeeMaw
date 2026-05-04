-- ============================================================================
-- 0003_extraction_worker_support.down.sql
-- Roll back the Extraction Worker support tables.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS processed_extractions_person_id_idx;
DROP TABLE IF EXISTS processed_extractions;

COMMIT;
