-- ============================================================================
-- 0003_extraction_worker_support.up.sql
-- Extraction Worker (step 11) — supporting tables.
-- ----------------------------------------------------------------------------
-- The Extraction Worker is an SQS consumer. SQS guarantees at-least-once
-- delivery, so we need an idempotency surface. We key on the SQS MessageId:
-- the first transaction that successfully extracts a segment writes the row;
-- a redelivery sees the row and ack-and-skips.
--
-- session_id has no FK because session metadata is owned by Node (DynamoDB).
-- person_id is a real FK because persons live here.
-- ============================================================================

BEGIN;

CREATE TABLE processed_extractions (
    sqs_message_id  TEXT PRIMARY KEY,
    person_id       UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    session_id      UUID NOT NULL,
    moments_written INT  NOT NULL DEFAULT 0,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX processed_extractions_person_id_idx
    ON processed_extractions (person_id, processed_at DESC);

COMMIT;
