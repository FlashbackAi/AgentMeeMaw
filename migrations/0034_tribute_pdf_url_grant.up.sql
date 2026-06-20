-- ============================================================================
-- 0034_tribute_pdf_url_grant.up.sql
-- Flashback AI: Legacy Mode  -  node_readonly UPDATE grant for tributes.pdf_url
-- ----------------------------------------------------------------------------
-- Migration 0033 added tributes.pdf_url but no GRANT. Node writes the URL
-- columns as the node_readonly role (LEGACY_PG_USER) and setRenderUrls issues
--   UPDATE tributes SET video_url = $3, pdf_url = $4
-- in one statement. Postgres checks column UPDATE privilege for EVERY column in
-- the SET list, so without UPDATE on pdf_url the whole write is rejected with
-- "permission denied" -- the tribute_render_complete handler then leaves both
-- video_url and pdf_url NULL even though status flipped to 'complete'.
--
-- The 2026-06-16 prod hotfix granted UPDATE (image_url, video_url,
-- thumbnail_url) ON tributes manually; pdf_url is the column that fix predates.
-- We re-grant the full URL-column set here (idempotent) so any environment that
-- missed the hotfix is also correct. Guarded by role existence so the migration
-- is a no-op on local/CI Postgres where node_readonly does not exist (mirrors
-- migration 0029).
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        GRANT UPDATE (image_url, video_url, thumbnail_url, pdf_url)
            ON tributes TO node_readonly;
    END IF;
END $$;

COMMIT;
