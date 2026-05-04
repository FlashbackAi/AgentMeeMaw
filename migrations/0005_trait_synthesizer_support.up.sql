-- ============================================================================
-- 0005_trait_synthesizer_support.up.sql
-- Trait Synthesizer (step 13) — supporting table.
-- ----------------------------------------------------------------------------
-- The Trait Synthesizer is an SQS consumer (queue: trait_synthesizer).
-- Like processed_extractions, idempotency keys are scoped to a TEXT primary
-- key so the same table serves both code paths:
--   * SQS path:    idempotency_key = SQS MessageId (string)
--   * CLI path:    idempotency_key = "runonce-{person_id}-{ms_timestamp}"
--
-- person_id has a real FK; the table is dropped if a person is deleted.
-- ============================================================================

BEGIN;

CREATE TABLE processed_trait_syntheses (
    idempotency_key   TEXT PRIMARY KEY,
    person_id         UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    traits_created    INT  NOT NULL DEFAULT 0,
    traits_upgraded   INT  NOT NULL DEFAULT 0,
    traits_downgraded INT  NOT NULL DEFAULT 0,
    processed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX processed_trait_syntheses_person_id_idx
    ON processed_trait_syntheses (person_id, processed_at DESC);

COMMIT;
