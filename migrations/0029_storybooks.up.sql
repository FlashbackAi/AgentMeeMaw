-- ============================================================================
-- 0029_storybooks.up.sql
-- Flashback AI: Legacy Mode  -  Standalone storybook layer (v2)
-- ----------------------------------------------------------------------------
-- A storybook is a general keepsake "book of memories" compiled from the
-- ENTIRE legacy memory (the qualifying active moments), assembled + captioned
-- by the same Sonnet assembler the tribute uses and rendered by the same PDF
-- renderer. Unlike a tribute it has NO contributor message and NO readiness
-- checklist -- it stands alone.
--
-- A legacy can have MANY storybooks over time. New editions are minted
-- automatically at session wrap, gated on enough NEW qualifying moments since
-- the last edition (see persons.moments_at_last_storybook_run). All editions
-- are kept; the read surface is a newest-first gallery.
--
-- Adds:
--   * storybooks table (agent owns writes; Node writes only the URL columns)
--   * persons.moments_at_last_storybook_run  (count-gate watermark, mirrors
--     moments_at_last_thread_run)
--   * active_storybooks view (Node read surface; excludes superseded)
--   * node_readonly grants, guarded by role existence so the same migration is
--     safe on local/CI (where the role does not exist) and prod (where it does)
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- storybooks table
-- ----------------------------------------------------------------------------

CREATE TABLE storybooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,

    title            TEXT,        -- short edition title (assembler-derived)
    script           JSONB,       -- assembled scenes/captions
    scene_moment_ids UUID[],      -- which moments became pages
    moments_count    INT,         -- qualifying-moment count at assembly time

    status TEXT NOT NULL DEFAULT 'generating'
        CHECK (status IN ('generating', 'complete', 'failed', 'superseded')),

    -- Node writes URLs; the agent only ever writes prompts/context (CLAUDE.md
    -- section 3). The PDF lives at the derived key (cover key + '.pdf').
    image_url     TEXT,
    thumbnail_url TEXT,
    generation_prompt         TEXT,
    latest_generation_context JSONB,  -- the storybook context (NOT keyed by kind)

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX storybooks_person_recent_idx ON storybooks (person_id, created_at DESC);
CREATE INDEX storybooks_person_status_idx ON storybooks (person_id, status);

CREATE TRIGGER trg_storybooks_updated_at BEFORE UPDATE ON storybooks
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ----------------------------------------------------------------------------
-- persons.moments_at_last_storybook_run  (count-gate watermark)
-- ----------------------------------------------------------------------------

ALTER TABLE persons
    ADD COLUMN IF NOT EXISTS moments_at_last_storybook_run INT NOT NULL DEFAULT 0;

-- ----------------------------------------------------------------------------
-- active_storybooks view (Node read surface)
-- ----------------------------------------------------------------------------

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

-- ----------------------------------------------------------------------------
-- node_readonly grants (guarded -- role exists only on deployed Postgres)
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        GRANT SELECT ON storybooks TO node_readonly;
        GRANT SELECT ON active_storybooks TO node_readonly;
        GRANT UPDATE (image_url, thumbnail_url) ON storybooks TO node_readonly;
    END IF;
END $$;

COMMIT;
