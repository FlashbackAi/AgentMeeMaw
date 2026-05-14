-- 0019_rename_starter_anchor_to_coverage_tap.down.sql
-- Restore the former starter_anchor source label.

ALTER TABLE questions DROP CONSTRAINT IF EXISTS questions_source_check;
ALTER TABLE questions DROP CONSTRAINT IF EXISTS chk_questions_person_scope;

UPDATE questions
SET source = 'starter_anchor'
WHERE source = 'coverage_tap';

ALTER TABLE questions
    ADD CONSTRAINT questions_source_check CHECK (source IN (
        'starter_anchor',
        'dropped_reference',
        'underdeveloped_entity',
        'life_period_gap',
        'thread_deepen',
        'universal_dimension'
    ));

ALTER TABLE questions
    ADD CONSTRAINT chk_questions_person_scope CHECK (
        (source = 'starter_anchor' AND person_id IS NULL)
        OR
        (source <> 'starter_anchor' AND person_id IS NOT NULL)
    );

DROP INDEX IF EXISTS questions_coverage_tap_dimension_idx;
CREATE INDEX questions_starter_dimension_idx
    ON questions ((attributes->>'dimension'))
    WHERE source = 'starter_anchor' AND status = 'active';
