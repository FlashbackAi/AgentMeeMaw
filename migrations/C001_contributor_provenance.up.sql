-- ============================================================================
-- C001_contributor_provenance.up.sql
-- Collaborator Phase 1, sub-project 1: provenance foundation.
-- ----------------------------------------------------------------------------
-- Every contributor-authored row records the Node user who authored it.
-- NULL means "creator era" (rows written before multi-contributor existed,
-- or rows produced without a session user — seeded questions, cadence
-- producer runs). No backfill, by design.
--
-- Semantics per table (spec D3):
--   moments.told_by_user_id        — told by (load-bearing: attribution,
--                                    retrieval bias, removal, conflicts)
--   moments.told_by_display_name   — denormalized for attribution rendering
--   entities.told_by_user_id       — first introduced by (informational)
--   traits.told_by_user_id         — first asserted by (informational)
--   questions.told_by_user_id      — whose session motivated it
--   profile_facts.told_by_user_id  — whose session produced the answer
--   processed_extractions.told_by_user_id — segment bookkeeping
--
-- Only moments.told_by_user_id ever drives hiding/removal (spec D4).
-- ============================================================================

BEGIN;

ALTER TABLE moments
    ADD COLUMN told_by_user_id      UUID NULL,
    ADD COLUMN told_by_display_name TEXT NULL;

ALTER TABLE entities
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE traits
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE questions
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE profile_facts
    ADD COLUMN told_by_user_id UUID NULL;

ALTER TABLE processed_extractions
    ADD COLUMN told_by_user_id UUID NULL;

-- Speaker-first retrieval (sub-project 2) and removal (sub-project 6)
-- both filter on exactly (person_id, told_by_user_id) over active rows.
CREATE INDEX moments_person_told_by_active_idx
    ON moments (person_id, told_by_user_id)
    WHERE status = 'active';

COMMIT;
