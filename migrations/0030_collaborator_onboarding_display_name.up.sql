ALTER TABLE collaborator_onboarding
    ADD COLUMN IF NOT EXISTS display_name TEXT;

-- Recreate the view so SELECT * picks up the new column.
CREATE OR REPLACE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';
