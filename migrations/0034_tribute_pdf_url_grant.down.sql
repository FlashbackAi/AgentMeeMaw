-- ============================================================================
-- 0034_tribute_pdf_url_grant.down.sql
-- Reverse 0034: drop only the pdf_url UPDATE grant this migration introduced.
-- The image_url/video_url/thumbnail_url grants predate it (2026-06-16 hotfix)
-- and are left intact. Guarded by role existence.
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        REVOKE UPDATE (pdf_url) ON tributes FROM node_readonly;
    END IF;
END $$;

COMMIT;
