-- ============================================================================
-- C004_expose_told_by_on_active_views.up.sql
-- Collaborator feature Phase 1, sub-project 3 (companion).
-- ----------------------------------------------------------------------------
-- active_entities / active_traits / active_questions / active_profile_facts
-- were created as SELECT * before told_by_user_id existed (C001); Postgres
-- freezes SELECT* column lists at view-creation time, so the column never
-- appeared in these views. Re-freeze them at the current schema (which
-- includes told_by_user_id). active_moments is handled in C002.
--
-- CREATE OR REPLACE, deliberately -- NOT "DROP VIEW" + "CREATE VIEW":
--   The C-series sorts AFTER the whole numbered sequence, so by the time this
--   runs, 0027/0030/0033's tribute_status view sits on top of these views and
--   a plain DROP fails outright ("cannot drop view active_entities because
--   other objects depend on it"). CREATE OR REPLACE re-freezes the column
--   list while leaving every dependent view untouched.
--
--   This works because ALTER TABLE ADD COLUMN appends: the original 0001
--   columns keep their names, types, and order at the front of SELECT *, and
--   everything added since (0023's latest_generation_context, C001's
--   told_by_user_id, ...) lands after them -- exactly the "trailing columns
--   only" rule CREATE OR REPLACE enforces.
-- ============================================================================

BEGIN;

CREATE OR REPLACE VIEW active_entities AS SELECT * FROM entities WHERE status = 'active';

CREATE OR REPLACE VIEW active_traits AS SELECT * FROM traits WHERE status = 'active';

CREATE OR REPLACE VIEW active_questions AS SELECT * FROM questions WHERE status = 'active';

CREATE OR REPLACE VIEW active_profile_facts AS SELECT * FROM profile_facts WHERE status = 'active';

COMMIT;
