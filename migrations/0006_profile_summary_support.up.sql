-- ============================================================================
-- 0006_profile_summary_support.up.sql
-- Profile Summary Generator (step 14) — supporting table.
-- ----------------------------------------------------------------------------
-- The Profile Summary Generator is an SQS consumer (queue: profile_summary).
-- Like processed_trait_syntheses, idempotency keys are scoped to a TEXT
-- primary key so the same table serves both code paths:
--   * SQS path:    idempotency_key = SQS MessageId (string)
--   * CLI path:    idempotency_key = "runonce-{person_id}-{ms_timestamp}"
--
-- The generated summary itself lives on persons.profile_summary (already
-- present from migration 0001). This table is purely an idempotency log;
-- summary_chars=0 marks an empty-legacy short-circuit (no LLM call, no
-- summary text).
-- ============================================================================

BEGIN;

CREATE TABLE processed_profile_summaries (
    idempotency_key  TEXT PRIMARY KEY,
    person_id        UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    summary_chars    INT  NOT NULL,
    processed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX processed_profile_summaries_person_id_idx
    ON processed_profile_summaries (person_id, processed_at DESC);

COMMIT;
