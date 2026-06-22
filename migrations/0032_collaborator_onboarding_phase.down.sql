DROP VIEW IF EXISTS active_collaborator_onboarding;

ALTER TABLE collaborator_onboarding
    DROP COLUMN IF EXISTS phase_locked_at;

ALTER TABLE collaborator_onboarding
    DROP COLUMN IF EXISTS phase;

CREATE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';
