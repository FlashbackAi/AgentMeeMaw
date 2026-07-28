BEGIN;

DROP VIEW IF EXISTS tribute_status;
DROP TABLE IF EXISTS tributes;

-- Restore the pre-0027 themes constraints (universal/emergent only).
ALTER TABLE themes DROP CONSTRAINT IF EXISTS chk_themes_kind_thread;
ALTER TABLE themes
    ADD CONSTRAINT chk_themes_kind_thread CHECK (
        (kind = 'universal' AND thread_id IS NULL)
        OR
        (kind = 'emergent'  AND thread_id IS NOT NULL)
    );

ALTER TABLE themes DROP CONSTRAINT IF EXISTS themes_kind_check;
ALTER TABLE themes
    ADD CONSTRAINT themes_kind_check
    CHECK (kind IN ('universal', 'emergent'));

COMMIT;
