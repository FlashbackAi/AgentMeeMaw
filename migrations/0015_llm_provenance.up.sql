-- ============================================================================
-- 0015_llm_provenance.up.sql
-- Record LLM model + prompt version provenance on generated rows.
-- ============================================================================

BEGIN;

ALTER TABLE moments
    ADD COLUMN IF NOT EXISTS llm_provider TEXT,
    ADD COLUMN IF NOT EXISTS llm_model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT;

ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS llm_provider TEXT,
    ADD COLUMN IF NOT EXISTS llm_model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT;

ALTER TABLE traits
    ADD COLUMN IF NOT EXISTS llm_provider TEXT,
    ADD COLUMN IF NOT EXISTS llm_model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT;

ALTER TABLE threads
    ADD COLUMN IF NOT EXISTS llm_provider TEXT,
    ADD COLUMN IF NOT EXISTS llm_model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS llm_provider TEXT,
    ADD COLUMN IF NOT EXISTS llm_model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT;

ALTER TABLE profile_facts
    ADD COLUMN IF NOT EXISTS llm_provider TEXT,
    ADD COLUMN IF NOT EXISTS llm_model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT;

COMMIT;
