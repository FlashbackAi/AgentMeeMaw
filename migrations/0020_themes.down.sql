-- ============================================================================
-- 0020_themes.down.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS active_themes_with_tier;
DROP VIEW IF EXISTS active_themes;

-- Remove all themed_as edges (themes are being dropped; theme kind is being
-- removed from the enum, so any surviving 'theme' edges would violate the
-- CHECK constraint below).
DELETE FROM edges
 WHERE edge_type = 'themed_as'
    OR from_kind = 'theme'
    OR to_kind   = 'theme';

DROP TABLE IF EXISTS themes;

ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_from_kind_check;
ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_to_kind_check;
ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_edge_type_check;

ALTER TABLE edges
    ADD CONSTRAINT edges_from_kind_check
    CHECK (from_kind IN
        ('moment', 'entity', 'thread', 'trait', 'question', 'person'));
ALTER TABLE edges
    ADD CONSTRAINT edges_to_kind_check
    CHECK (to_kind IN
        ('moment', 'entity', 'thread', 'trait', 'question', 'person'));
ALTER TABLE edges
    ADD CONSTRAINT edges_edge_type_check
    CHECK (edge_type IN (
        'involves',
        'happened_at',
        'exemplifies',
        'evidences',
        'related_to',
        'motivated_by',
        'targets',
        'answered_by'
    ));

COMMIT;
