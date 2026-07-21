-- 0045_visual_theme_render_engine.down.sql  -  reverse 0045

BEGIN;

ALTER TABLE tribute_visual_themes
    DROP COLUMN IF EXISTS render_engine;

COMMIT;
