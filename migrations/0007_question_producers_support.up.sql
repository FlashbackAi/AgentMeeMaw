-- ============================================================================
-- 0007_question_producers_support.up.sql
-- Question Producers P2/P3/P5 (step 15) - idempotency support.
-- ----------------------------------------------------------------------------
-- One table backs both queue and CLI paths:
--   * SQS path: idempotency_key = SQS MessageId
--   * CLI path: idempotency_key = "runonce-{producer}-{person_id}-{ms}"
-- ============================================================================

BEGIN;

CREATE TABLE processed_producer_runs (
    idempotency_key   TEXT PRIMARY KEY,
    person_id         UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    producer          TEXT NOT NULL CHECK (producer IN ('P2', 'P3', 'P5')),
    questions_written INT NOT NULL DEFAULT 0,
    processed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX processed_producer_runs_person_idx
    ON processed_producer_runs (person_id, producer, processed_at DESC);

COMMIT;
