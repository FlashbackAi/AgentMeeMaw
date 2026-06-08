-- 0025_extraction_completion_signal.down.sql
BEGIN;

DROP VIEW IF EXISTS session_extraction_status;

ALTER TABLE processed_extractions
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS is_final,
    DROP COLUMN IF EXISTS traits_written,
    DROP COLUMN IF EXISTS entities_written;

COMMIT;
