-- ============================================================================
-- 0044_flashback_recipe.down.sql  -  reverse 0044_flashback_recipe.up.sql
-- ============================================================================

BEGIN;

ALTER TABLE tribute_visual_themes
    DROP COLUMN IF EXISTS layout_palette,
    DROP COLUMN IF EXISTS layout_pins,
    DROP COLUMN IF EXISTS pacing,
    DROP COLUMN IF EXISTS motion_preset;

COMMIT;
