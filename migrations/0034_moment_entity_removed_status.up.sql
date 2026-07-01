-- SP6a: reversible collaborator removal hides moments/entities via a new
-- 'removed' status. The active_* views already filter status='active', so no
-- view changes are needed.

ALTER TABLE moments DROP CONSTRAINT moments_status_check;
ALTER TABLE moments ADD CONSTRAINT moments_status_check
    CHECK (status IN ('active', 'superseded', 'removed'));

ALTER TABLE entities DROP CONSTRAINT entities_status_check;
ALTER TABLE entities ADD CONSTRAINT entities_status_check
    CHECK (status IN ('active', 'merged', 'removed'));
