-- ============================================================================
-- 0028_collaborator_onboarding.up.sql
-- Collaborator feature Phase 1, sub-project 3: onboarding coverage signals.
-- ----------------------------------------------------------------------------
-- Agent-internal coverage signals for non-creator collaborators, keyed by
-- (person_id, user_id). DENORMALIZED MIRROR of the Node-side DynamoDB
-- membership row, which stays the source of truth for membership identity,
-- raw modal answers, and onboarding_complete. Fields are mirrored here at
-- session start (apply_collaborator_onboarding) so per-turn reads are local
-- single-row queries instead of cross-service lookups.
--
-- Columns for the deferred nudge / first-moment / removal flows are present
-- but unused this cycle (filled by later sub-projects). One active row per
-- (person_id, user_id).
-- ============================================================================

BEGIN;

CREATE TABLE collaborator_onboarding (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,

    -- Voice anchor — the contributor's relationship to the subject
    -- ("his daughter"). Used as the opener prior and the attribution
    -- relationship phrase. Filled from the modal answer (mirrored from
    -- DynamoDB at session start) or, later, from a tap-card answer.
    voice_anchor_text TEXT,
    voice_anchored_at TIMESTAMPTZ,

    -- First-moment marker — flipped by the extraction worker when the first
    -- moment with told_by_user_id = this user_id commits. DEFERRED this cycle.
    first_moment_id UUID REFERENCES moments(id),
    first_moment_recorded_at TIMESTAMPTZ,

    -- Modal state mirror — denormalized from DynamoDB at session start.
    modal_answered_at TIMESTAMPTZ,
    modal_dismissed_at TIMESTAMPTZ,

    -- Agent-internal tap counter for the deferred 3-nudge cap. DEFERRED.
    taps_emitted INT NOT NULL DEFAULT 0,

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'removed')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        (voice_anchored_at IS NULL AND voice_anchor_text IS NULL)
        OR (voice_anchored_at IS NOT NULL AND voice_anchor_text IS NOT NULL)
    ),
    CHECK (
        (first_moment_id IS NULL AND first_moment_recorded_at IS NULL)
        OR (first_moment_id IS NOT NULL AND first_moment_recorded_at IS NOT NULL)
    )
);

CREATE TRIGGER trg_collaborator_onboarding_updated_at
    BEFORE UPDATE ON collaborator_onboarding
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- One active onboarding row per contributor per memorial. A removed row
-- stays for audit; a re-invite gets a NEW active row.
CREATE UNIQUE INDEX uq_collaborator_onboarding_active
    ON collaborator_onboarding (person_id, user_id)
    WHERE status = 'active';

CREATE INDEX idx_collaborator_onboarding_person_user
    ON collaborator_onboarding (person_id, user_id, status);

CREATE VIEW active_collaborator_onboarding AS
    SELECT *
    FROM collaborator_onboarding
    WHERE status = 'active';

COMMIT;
