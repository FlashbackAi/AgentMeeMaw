-- ============================================================================
-- C004_expose_told_by_on_active_views.down.sql
-- Re-freezes the four views as SELECT * (functionally identical to the up; the
-- pre-C004 frozen column set cannot be reconstructed).
--
-- CREATE OR REPLACE rather than DROP + CREATE, for the same reason as the up
-- migration: 0027/0030/0033's tribute_status depends on these views once the
-- C-series runs last, and a plain DROP would fail.
--
-- Caveat (pre-dates the C-series rename): because these views are SELECT *,
-- this migration cannot un-expose told_by_user_id. A full rollback past C001
-- therefore has to drop the dependent views first, or C001's
-- "ALTER TABLE ... DROP COLUMN told_by_user_id" will be refused while a view
-- still references the column.
-- ============================================================================

BEGIN;

CREATE OR REPLACE VIEW active_entities AS SELECT * FROM entities WHERE status = 'active';

CREATE OR REPLACE VIEW active_traits AS SELECT * FROM traits WHERE status = 'active';

CREATE OR REPLACE VIEW active_questions AS SELECT * FROM questions WHERE status = 'active';

CREATE OR REPLACE VIEW active_profile_facts AS SELECT * FROM profile_facts WHERE status = 'active';

COMMIT;
