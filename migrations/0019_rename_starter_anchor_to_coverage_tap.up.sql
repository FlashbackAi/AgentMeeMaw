-- 0019_rename_starter_anchor_to_coverage_tap.up.sql
-- Retire starter_anchor templates and relabel them as structured coverage taps.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM questions
        WHERE source = 'starter_anchor'
          AND (
              attributes->>'dimension' IS NULL
              OR attributes->'themes' IS NULL
              OR jsonb_typeof(attributes->'themes') <> 'array'
              OR jsonb_array_length(attributes->'themes') = 0
          )
    ) THEN
        RAISE EXCEPTION 'starter_anchor rows must have attributes.dimension and non-empty attributes.themes before coverage_tap relabel';
    END IF;
END $$;

ALTER TABLE questions DROP CONSTRAINT IF EXISTS questions_source_check;
ALTER TABLE questions DROP CONSTRAINT IF EXISTS chk_questions_person_scope;

UPDATE questions
SET source = 'coverage_tap'
WHERE source = 'starter_anchor';

ALTER TABLE questions
    ADD CONSTRAINT questions_source_check CHECK (source IN (
        'coverage_tap',
        'dropped_reference',
        'underdeveloped_entity',
        'life_period_gap',
        'thread_deepen',
        'universal_dimension'
    ));

ALTER TABLE questions
    ADD CONSTRAINT chk_questions_person_scope CHECK (
        (source = 'coverage_tap' AND person_id IS NULL)
        OR
        (source <> 'coverage_tap' AND person_id IS NOT NULL)
    );

DROP INDEX IF EXISTS questions_starter_dimension_idx;
CREATE INDEX questions_coverage_tap_dimension_idx
    ON questions ((attributes->>'dimension'))
    WHERE source = 'coverage_tap' AND status = 'active';
