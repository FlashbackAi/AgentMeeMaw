-- ============================================================================
-- 0025_extraction_completion_signal.up.sql
-- Extraction-completion signal: durable per-segment status + read view.
-- ----------------------------------------------------------------------------
-- The Extraction Worker emits a transactional pg_notify('extraction_complete')
-- when a segment finishes. Postgres is authoritative (this status row); the
-- notification is the low-latency wake-up only. A zero-moment segment still
-- writes a row and still notifies, which is what lets the UI distinguish
-- "extraction finished, nothing extracted" from "still running".
--
-- `is_final` marks the wrap-forced tail segment of a session (invariant #12).
-- `status` is 'done' on the happy path; reserved for future 'failed' states.
-- ============================================================================

BEGIN;

ALTER TABLE processed_extractions
    ADD COLUMN entities_written INT     NOT NULL DEFAULT 0,
    ADD COLUMN traits_written   INT     NOT NULL DEFAULT 0,
    ADD COLUMN is_final         BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN status           TEXT    NOT NULL DEFAULT 'done';

-- Node-facing read surface. One row per extracted segment; Node groups by
-- session_id and aggregates (sum moments, bool_or(is_final)). Exposing a view
-- rather than the raw idempotency table decouples Node from our internal
-- mechanism.
CREATE VIEW session_extraction_status AS
SELECT
    session_id,
    person_id,
    sqs_message_id AS segment_message_id,
    moments_written,
    entities_written,
    traits_written,
    is_final,
    status,
    processed_at
FROM processed_extractions;

COMMIT;
