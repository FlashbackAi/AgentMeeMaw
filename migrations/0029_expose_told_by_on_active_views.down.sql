-- ============================================================================
-- 0029_expose_told_by_on_active_views.down.sql
-- Recreates the four views as SELECT * (functionally identical; the pre-0029
-- frozen column set cannot be reconstructed and need not be).
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
