-- ============================================================================
-- 0035_storybook_python_render.up.sql
-- Flashback AI: Legacy Mode  -  Storybooks: Python-owned render
-- ----------------------------------------------------------------------------
-- Per spec 2026-06-29 (validated by the storybook_comic_prototype spike): the
-- agent's storybook_render worker curates + assembles + renders the book
-- (PDF + per-page PNGs) and uploads via Node-minted presigned URLs; Node
-- LISTENs storybook_render_complete and writes pdf_url + page_urls (plus the
-- cover image_url / thumbnail_url). The old artifact_generation path for
-- storybooks is retired. Status CHECK from 0029 already includes 'failed'.
--
-- Adds:
--   * storybooks.collection    -- chosen collection slug (childhood, ...)
--   * storybooks.pdf_url       -- Node-written (book PDF)
--   * storybooks.page_urls     -- Node-written ordered page PNG URLs
--   * storybooks.rendered_at   -- worker completion stamp
--   * storybooks.render_error  -- terminal failure reason (worker-written)
--   * active_storybooks view recreated with the new read columns appended
--   * node_readonly UPDATE grant extended to pdf_url + page_urls
-- ============================================================================

BEGIN;

ALTER TABLE storybooks
    ADD COLUMN IF NOT EXISTS collection   TEXT,
    ADD COLUMN IF NOT EXISTS pdf_url      TEXT,
    ADD COLUMN IF NOT EXISTS page_urls    JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS rendered_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS render_error TEXT;

-- Append-only view recreation preserves the node_readonly SELECT grant
-- (CREATE OR REPLACE VIEW may only add trailing columns).
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
    tags,
    collection,
    pdf_url,
    page_urls,
    rendered_at
FROM storybooks
WHERE status <> 'superseded';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        GRANT UPDATE (image_url, thumbnail_url, pdf_url, page_urls)
            ON storybooks TO node_readonly;
    END IF;
END $$;

COMMIT;
