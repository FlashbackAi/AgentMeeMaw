-- Flip any removed rows back to active before narrowing the constraint, so
-- the tightened CHECK does not fail on existing data.
UPDATE moments  SET status = 'active' WHERE status = 'removed';
UPDATE entities SET status = 'active' WHERE status = 'removed';

ALTER TABLE moments DROP CONSTRAINT moments_status_check;
ALTER TABLE moments ADD CONSTRAINT moments_status_check
    CHECK (status IN ('active', 'superseded'));

ALTER TABLE entities DROP CONSTRAINT entities_status_check;
ALTER TABLE entities ADD CONSTRAINT entities_status_check
    CHECK (status IN ('active', 'merged'));
