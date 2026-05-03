-- ============================================================================
-- 0001_initial_schema.down.sql
-- Rollback for the initial schema. Drops everything created by .up.sql.
-- Does NOT drop the pgcrypto / vector extensions (may be in use elsewhere).
-- ============================================================================

BEGIN;

-- Views
DROP VIEW IF EXISTS active_edges;
DROP VIEW IF EXISTS active_questions;
DROP VIEW IF EXISTS active_traits;
DROP VIEW IF EXISTS active_threads;
DROP VIEW IF EXISTS active_entities;
DROP VIEW IF EXISTS active_moments;
DROP VIEW IF EXISTS active_persons;

-- Tables (FKs are CASCADE; drop order minimizes warnings)
DROP TABLE IF EXISTS moment_history;
DROP TABLE IF EXISTS edges;
DROP TABLE IF EXISTS questions;
DROP TABLE IF EXISTS traits;
DROP TABLE IF EXISTS threads;
DROP TABLE IF EXISTS entities;
DROP TABLE IF EXISTS moments;
DROP TABLE IF EXISTS persons;

-- Helper function
DROP FUNCTION IF EXISTS trg_set_updated_at();

COMMIT;
