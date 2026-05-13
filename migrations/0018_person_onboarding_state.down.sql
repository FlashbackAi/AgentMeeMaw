-- ============================================================================
-- Revert 0018 person-level onboarding state.
-- ============================================================================

BEGIN;

ALTER TABLE persons
    DROP COLUMN IF EXISTS archetype_answers,
    DROP COLUMN IF EXISTS onboarding_complete;

COMMIT;
