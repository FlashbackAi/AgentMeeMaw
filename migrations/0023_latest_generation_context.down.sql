BEGIN;

ALTER TABLE threads  DROP COLUMN IF EXISTS latest_generation_context;
ALTER TABLE entities DROP COLUMN IF EXISTS latest_generation_context;
ALTER TABLE moments  DROP COLUMN IF EXISTS latest_generation_context;
ALTER TABLE persons  DROP COLUMN IF EXISTS latest_generation_context;

COMMIT;
