-- ============================================================================
-- 0049_usage_events_person_id_index.up.sql
-- Per-user cost attribution index for the Phase 21 users dashboard.
-- Node's per-user drill-down runs `WHERE person_id = ANY($1)` against the
-- append-only usage_events ledger on every request; without this index that
-- is a seq scan per drill-down. usage_events writes are best-effort inserts
-- off the critical path (metering never breaks a render), so the brief
-- build-time lock is acceptable and we stay on the repo's transactional-
-- migration convention rather than CREATE INDEX CONCURRENTLY.
-- ============================================================================

BEGIN;

CREATE INDEX IF NOT EXISTS usage_events_person_id_idx
    ON usage_events (person_id)
    WHERE person_id IS NOT NULL;

COMMIT;
