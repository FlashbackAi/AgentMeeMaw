BEGIN;

-- Remove backfilled tribute themes. Safe: tribute themes carry no
-- thread_id and are recreated by insert_person / 0028 up on demand.
DELETE FROM themes WHERE kind = 'tribute';

COMMIT;
