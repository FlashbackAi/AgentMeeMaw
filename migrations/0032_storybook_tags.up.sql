-- ============================================================================
-- 0032_storybook_tags.up.sql
-- Flashback AI: Legacy Mode  -  Emotional tags on storybooks
-- ----------------------------------------------------------------------------
-- Storybooks are now minted ON DEMAND (no longer auto-generated at session
-- wrap) and a legacy can hold many of them. Each storybook carries one or more
-- emotional tags from a fixed code-side registry (flashback.storybook.tags):
-- the Sonnet assembler picks the 1-3 that fit the chosen moments and tones the
-- captions accordingly, and Node maps the stable slugs to render templates.
--
-- persons.moments_at_last_storybook_run (migration 0029) is now UNUSED -- the
-- count-gate it backed is gone with auto-generation. It is left in place (no
-- destructive change) so old rows and the down migration stay simple.
--
-- Adds:
--   * storybooks.tags TEXT[]  (emotional tag slugs; '{}' until assembled)
--   * active_storybooks view recreated to expose tags to Node
-- ============================================================================

BEGIN;

ALTER TABLE storybooks
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

-- Recreate the Node read surface with tags appended (CREATE OR REPLACE keeps
-- the existing node_readonly SELECT grant).
-- Appended at the end: CREATE OR REPLACE VIEW may only add trailing columns
-- (it cannot reorder existing ones), and appending preserves the grant.
CREATE OR REPLACE VIEW active_storybooks AS
SELECT
    id,
    person_id,
    title,
    status,
    moments_count,
    image_url,
    thumbnail_url,
    created_at,
    updated_at,
    tags
FROM storybooks
WHERE status <> 'superseded';

COMMIT;
