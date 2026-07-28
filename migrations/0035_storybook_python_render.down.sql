-- ============================================================================
-- 0035_storybook_python_render.down.sql
-- Revert the Python-owned storybook render columns + view shape.
-- ============================================================================

BEGIN;

-- CREATE OR REPLACE VIEW cannot drop trailing columns, and the view must not
-- reference them before the columns are dropped -- so drop + recreate +
-- re-grant, mirroring migration 0032's down.
DROP VIEW IF EXISTS active_storybooks;

CREATE VIEW active_storybooks AS
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

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        GRANT SELECT ON active_storybooks TO node_readonly;
    END IF;
END $$;

ALTER TABLE storybooks
    DROP COLUMN IF EXISTS collection,
    DROP COLUMN IF EXISTS pdf_url,
    DROP COLUMN IF EXISTS page_urls,
    DROP COLUMN IF EXISTS rendered_at,
    DROP COLUMN IF EXISTS render_error;

COMMIT;
