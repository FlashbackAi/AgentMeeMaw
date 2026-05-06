-- ============================================================================
-- 0014_extraction_outbox.up.sql
-- Durable fan-out for Extraction Worker post-commit SQS jobs.
-- ============================================================================

BEGIN;

CREATE TABLE extraction_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_sqs_message_id TEXT NOT NULL REFERENCES processed_extractions(sqs_message_id)
        ON DELETE CASCADE,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL CHECK (
        job_type IN ('embedding', 'artifact', 'thread_detector')
    ),
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'in_progress', 'sent')
    ),
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX extraction_outbox_pending_idx
    ON extraction_outbox (available_at, created_at)
    WHERE status = 'pending';

CREATE INDEX extraction_outbox_stale_in_progress_idx
    ON extraction_outbox (updated_at)
    WHERE status = 'in_progress';

CREATE INDEX extraction_outbox_source_message_idx
    ON extraction_outbox (source_sqs_message_id);

COMMIT;
