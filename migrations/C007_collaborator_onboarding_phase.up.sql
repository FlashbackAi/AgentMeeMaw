ALTER TABLE collaborator_onboarding
    ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'onboarding'
        CHECK (phase IN ('onboarding', 'active'));

ALTER TABLE collaborator_onboarding
    ADD COLUMN IF NOT EXISTS phase_locked_at TIMESTAMPTZ;

-- Recreate the SELECT * view so it picks up the new columns.
CREATE OR REPLACE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';
