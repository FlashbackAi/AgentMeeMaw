-- ============================================================================
-- 0045_visual_theme_render_engine.up.sql
-- Flashback AI: Legacy Mode  -  per-theme render engine pin
-- ----------------------------------------------------------------------------
-- RENDER_ENGINE=remotion is now the worker's global default, which would
-- restyle EVERY occasion's video on its next render. Some occasions must keep
-- the legacy Pillow/ffmpeg look (Father's Day ships the classic framed
-- slideshow) -- so the visual theme gains an optional engine pin that rides
-- the snapshot style dict like the other recipe levers:
--
--   * render_engine : '' (worker default) | 'legacy' | 'remotion'
--
-- Empty keeps the worker's default. The pin is snapshotted at /generate, so
-- flipping it in the CRM only affects future renders / manual regenerates.
-- ============================================================================

BEGIN;

ALTER TABLE tribute_visual_themes
    ADD COLUMN IF NOT EXISTS render_engine TEXT NOT NULL DEFAULT '';

COMMIT;
