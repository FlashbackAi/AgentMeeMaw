-- ============================================================================
-- 0028_backfill_tribute_theme.up.sql
-- Flashback AI: Legacy Mode  -  Tribute theme backfill
-- ----------------------------------------------------------------------------
-- The tribute theme (kind='tribute', migration 0027) is now seeded for every
-- new legacy in insert_person, alongside the universals. This backfills it
-- for legacies created before that change, so the tribute is discoverable via
-- the standard unlock sequence (active_themes_with_tier -> unlock_prepare ->
-- session/start) -- no special entry endpoint.
--
-- Idempotent via the active-slug partial unique index + ON CONFLICT DO NOTHING.
-- Slug/display/description mirror flashback/tribute/theme.py.
-- ============================================================================

BEGIN;

INSERT INTO themes (person_id, kind, slug, display_name, description, state)
SELECT p.id,
       'tribute',
       'tribute',
       'A Tribute',
       'A short, shareable tribute to them -- a handful of shared memories '
       'and one thing you''d want to say straight to them.',
       'locked'
  FROM persons p
ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING;

COMMIT;
