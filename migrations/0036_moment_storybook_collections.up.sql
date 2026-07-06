-- ============================================================================
-- 0036_moment_storybook_collections.up.sql
-- Flashback AI: Legacy Mode  -  Per-moment storybook collection tags
-- ----------------------------------------------------------------------------
-- A storybook could be minted for a collection that NONE of a person's moments
-- actually fit (the mint gate was pool-wide; per-collection fit was decided
-- later by the curation LLM in the render worker, which fell back to the whole
-- unrelated pool). That produced hallucinated books.
--
-- The fix: the Extraction Worker tags each moment with the grid collections it
-- genuinely fits, stored here. Eligibility becomes a deterministic SQL count
-- and the render slice is resolved from tags (the curation LLM pass is retired).
-- Design: docs/superpowers/specs/2026-07-06-storybook-collection-eligibility-design.md
--
-- NULL vs '{}' is load-bearing:
--   NULL -> never tagged (extracted before this feature); the backfill selects
--           these rows.
--   '{}' -> tagged, fits no collection (a genuine result; re-runs skip it).
--
-- The chapter collection 'wisdom' is deliberately NOT tagged or gated per
-- collection: it lenses the whole qualifying pool as before.
--
-- Adds:
--   * moments.storybook_collections TEXT[] NULL
--   * GIN index for `= ANY(storybook_collections)` eligibility counts
--
-- Note: the storybook repository reads this column from the base ``moments``
-- table with an explicit ``status = 'active'`` filter, NOT via the
-- ``active_moments`` view. ``active_moments`` is a ``SELECT *`` view with its
-- own dependents (active_themes_with_tier, tribute_status); recreating it to
-- surface a new column can't be cleanly reversed in a down migration without
-- CASCADE-dropping those dependents. Reading the base table sidesteps that and
-- keeps this migration trivially reversible.
-- ============================================================================

BEGIN;

ALTER TABLE moments
    ADD COLUMN IF NOT EXISTS storybook_collections TEXT[] NULL;

CREATE INDEX IF NOT EXISTS idx_moments_storybook_collections
    ON moments USING GIN (storybook_collections);

COMMIT;
