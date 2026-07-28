-- ============================================================================
-- C002_active_moments_provenance_columns.up.sql
-- Collaborator Phase 1, sub-project 2: expose provenance columns in the
-- active_moments view so the retrieval layer can SELECT and ORDER BY them.
-- ----------------------------------------------------------------------------
-- active_moments was created in 0001 as SELECT * FROM moments … but Postgres
-- resolves * at view-creation time, so the two columns added in C001
-- (told_by_user_id, told_by_display_name) did not appear in the view.
--
-- CREATE OR REPLACE, deliberately -- NOT "DROP VIEW ... CASCADE":
--   The C-series sorts AFTER the whole numbered sequence, so by the time this
--   runs, other views have been built on top of active_moments (0022's
--   active_themes_with_tier and 0027/0030/0033's tribute_status). A CASCADE
--   drop here would silently delete them and this migration would have to
--   carry copies of their definitions -- copies that would then clobber the
--   numbered migrations' newer versions. CREATE OR REPLACE leaves every
--   dependent view untouched.
--
-- The price of CREATE OR REPLACE is that the existing columns must keep their
-- exact names, types, and ORDER, and new ones may only be APPENDED. Hence
-- told_by_user_id / told_by_display_name sit after updated_at rather than
-- before it. Nothing reads this view with SELECT * (every caller in src/ uses
-- an explicit column list), so view column order is not load-bearing.
--
-- storybook_collections (moments column, added by 0036) is deliberately NOT
-- listed: 0036 leaves active_moments untouched by design (the storybook
-- repository reads the base table with an explicit status filter) and
-- tests/db/test_migration_0036.py asserts the column is absent from the view.
-- ============================================================================

BEGIN;

CREATE OR REPLACE VIEW active_moments AS
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
    updated_at,
    told_by_user_id,
    told_by_display_name
FROM moments
WHERE status = 'active';

COMMIT;
