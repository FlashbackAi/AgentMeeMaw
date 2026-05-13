-- ============================================================================
-- 0018_person_onboarding_state.up.sql
-- Store v1 onboarding completion on persons, not person_roles.
-- ----------------------------------------------------------------------------
-- v1 has one contributor per legacy, so the agent does not require the
-- Node-owned multi-contributor role table to drive archetype onboarding.
-- ============================================================================

BEGIN;

ALTER TABLE persons
    ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archetype_answers JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
