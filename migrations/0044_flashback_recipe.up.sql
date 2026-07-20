-- ============================================================================
-- 0044_flashback_recipe.up.sql
-- Flashback AI: Legacy Mode  -  Remotion composition-engine recipe config
-- ----------------------------------------------------------------------------
-- Spec 2026-07-20 (flashback-composition-engine): the Remotion renderer picks
-- a scene LAYOUT per beat from a per-theme palette + role pins, at a themed
-- pacing and motion preset. These were hardcoded to the proven Friendship
-- default; a visual theme now carries them so a memorial != a friend.
--
--   * layout_palette : allowed layout slugs (split_duotone | scrapbook |
--                      type_over_crop | fullbleed_caption | framed_hero)
--   * layout_pins    : {opener|payoff|closing -> slug} structural pins
--   * pacing         : {hold, transition} seconds
--   * motion_preset  : calm | playful | punchy | cinematic
--
-- (The accent colour rides the existing ink JSONB as ink.accent -- no column.)
--
-- Empty defaults reproduce the code-side Friendship default at render time
-- (recipe_kwargs_from_style); a render never blocks on config. Renders read
-- these through the snapshot style dict like fonts/ink/audio.
--
-- NOTE: 0043 is skipped. It belonged to the parked wip/theme-motion-layout
-- branch (old Pillow/ffmpeg layout+transition config), which this Remotion
-- recipe supersedes -- that branch is retired, so the number is left as a gap
-- rather than renumbered (migration history stays append-only).
-- ============================================================================

BEGIN;

ALTER TABLE tribute_visual_themes
    ADD COLUMN IF NOT EXISTS layout_palette TEXT[]  NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS layout_pins    JSONB   NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS pacing         JSONB   NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS motion_preset  TEXT    NOT NULL DEFAULT '';

COMMIT;
