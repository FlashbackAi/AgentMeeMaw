-- ============================================================================
-- 0037_usage_events.down.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS dashboard_worker_health;
DROP VIEW IF EXISTS dashboard_content_counts;
DROP VIEW IF EXISTS dashboard_tributes;
DROP VIEW IF EXISTS dashboard_storybooks;
DROP VIEW IF EXISTS dashboard_cost_by_model;
DROP VIEW IF EXISTS dashboard_cost_by_feature;

DROP TABLE IF EXISTS usage_events;

COMMIT;
