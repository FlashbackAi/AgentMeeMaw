BEGIN;

-- CREATE OR REPLACE VIEW cannot drop the trailing `tags` column, and the view
-- must not reference it before the column is dropped -- so drop + recreate +
-- re-grant, mirroring migration 0029.
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
    updated_at
FROM storybooks
WHERE status <> 'superseded';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        GRANT SELECT ON active_storybooks TO node_readonly;
    END IF;
END $$;

ALTER TABLE storybooks DROP COLUMN IF EXISTS tags;

COMMIT;
