-- ============================================================================
-- 0029_expose_told_by_on_active_views.up.sql
-- Collaborator feature Phase 1, sub-project 3 (companion).
-- ----------------------------------------------------------------------------
-- active_entities / active_traits / active_questions / active_profile_facts
-- were created as SELECT * before told_by_user_id existed (0026); Postgres
-- freezes SELECT* column lists at view-creation time, so the column never
-- appeared in these views. Recreate them as SELECT * now to re-freeze at the
-- current schema (which includes told_by_user_id). active_moments was already
-- handled in 0027.
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_entities;
CREATE VIEW active_entities AS SELECT * FROM entities WHERE status = 'active';

DROP VIEW IF EXISTS active_traits;
CREATE VIEW active_traits AS SELECT * FROM traits WHERE status = 'active';

DROP VIEW IF EXISTS active_questions;
CREATE VIEW active_questions AS SELECT * FROM questions WHERE status = 'active';

DROP VIEW IF EXISTS active_profile_facts;
CREATE VIEW active_profile_facts AS SELECT * FROM profile_facts WHERE status = 'active';

COMMIT;
