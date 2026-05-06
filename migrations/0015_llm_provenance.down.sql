-- ============================================================================
-- 0015_llm_provenance.down.sql
-- Remove LLM provenance columns.
-- ============================================================================

BEGIN;

ALTER TABLE profile_facts
    DROP COLUMN IF EXISTS prompt_version,
    DROP COLUMN IF EXISTS llm_model,
    DROP COLUMN IF EXISTS llm_provider;

ALTER TABLE questions
    DROP COLUMN IF EXISTS prompt_version,
    DROP COLUMN IF EXISTS llm_model,
    DROP COLUMN IF EXISTS llm_provider;

ALTER TABLE threads
    DROP COLUMN IF EXISTS prompt_version,
    DROP COLUMN IF EXISTS llm_model,
    DROP COLUMN IF EXISTS llm_provider;

ALTER TABLE traits
    DROP COLUMN IF EXISTS prompt_version,
    DROP COLUMN IF EXISTS llm_model,
    DROP COLUMN IF EXISTS llm_provider;

ALTER TABLE entities
    DROP COLUMN IF EXISTS prompt_version,
    DROP COLUMN IF EXISTS llm_model,
    DROP COLUMN IF EXISTS llm_provider;

ALTER TABLE moments
    DROP COLUMN IF EXISTS prompt_version,
    DROP COLUMN IF EXISTS llm_model,
    DROP COLUMN IF EXISTS llm_provider;

COMMIT;
