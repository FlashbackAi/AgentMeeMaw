-- ============================================================================
-- C002_active_moments_provenance_columns.down.sql
-- ----------------------------------------------------------------------------
-- Removing columns from a view is not possible with CREATE OR REPLACE (it may
-- only APPEND trailing columns), so the view has to be dropped and rebuilt.
-- Dropping it requires CASCADE, because other views sit on top of it
-- (active_themes_with_tier from 0022, tribute_status from 0027/0030/0033).
--
-- Rather than hard-coding copies of those definitions -- which would go stale
-- the moment a numbered migration revises them -- this captures each
-- dependent view's live definition with pg_get_viewdef BEFORE the cascade and
-- replays it afterwards. Whatever was there is what comes back.
--
-- The columns must leave the view here so C001's down migration can DROP the
-- underlying moments.told_by_* columns (Postgres refuses to drop a column a
-- view still references).
-- ============================================================================

BEGIN;

DO $$
DECLARE
    dep         RECORD;
    saved_names TEXT[] := ARRAY[]::TEXT[];
    saved_defs  TEXT[] := ARRAY[]::TEXT[];
    idx         INT;
BEGIN
    FOR dep IN
        SELECT DISTINCT c.relname AS name, pg_get_viewdef(c.oid, true) AS def
          FROM pg_depend  d
          JOIN pg_rewrite r ON r.oid = d.objid
          JOIN pg_class   c ON c.oid = r.ev_class
          JOIN pg_class   s ON s.oid = d.refobjid
         WHERE s.relname  = 'active_moments'
           AND c.relname <> 'active_moments'
           AND c.relkind  = 'v'
    LOOP
        saved_names := saved_names || dep.name;
        saved_defs  := saved_defs  || dep.def;
    END LOOP;

    DROP VIEW IF EXISTS active_moments CASCADE;

    CREATE VIEW active_moments AS
    SELECT
        id,
        person_id,
        title,
        narrative,
        time_anchor,
        life_period_estimate,
        sensory_details,
        emotional_tone,
        contributor_perspective,
        status,
        superseded_by,
        narrative_embedding,
        embedding_model,
        embedding_model_version,
        video_url,
        thumbnail_url,
        generation_prompt,
        created_at,
        updated_at
    FROM moments
    WHERE status = 'active';

    FOR idx IN 1 .. COALESCE(array_length(saved_names, 1), 0) LOOP
        EXECUTE format('CREATE VIEW %I AS %s', saved_names[idx], saved_defs[idx]);
    END LOOP;
END $$;

COMMIT;
