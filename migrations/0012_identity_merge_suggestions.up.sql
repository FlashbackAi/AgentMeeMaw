-- ============================================================================
-- 0012_identity_merge_suggestions.up.sql
-- Flashback AI: Legacy Mode - user-approved entity merge workflow
-- ----------------------------------------------------------------------------
-- Stores proposed identity merges such as:
--   "Chithanya's girlfriend" -> "Madhav"
--
-- Extraction may create pending suggestions automatically, but the graph is
-- mutated only after an explicit approval call.
-- ============================================================================

BEGIN;

CREATE TABLE identity_merge_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,

    source_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,

    proposed_alias TEXT,
    reason TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'extraction'
        CHECK (source IN ('extraction', 'user_edit', 'admin')),

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),

    approved_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (source_entity_id <> target_entity_id),
    CHECK (
        (status = 'approved' AND approved_at IS NOT NULL)
        OR (status <> 'approved' AND approved_at IS NULL)
    ),
    CHECK (
        (status = 'rejected' AND rejected_at IS NOT NULL)
        OR (status <> 'rejected' AND rejected_at IS NULL)
    )
);

CREATE TRIGGER trg_identity_merge_suggestions_updated_at
    BEFORE UPDATE ON identity_merge_suggestions
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE INDEX identity_merge_suggestions_person_status_idx
    ON identity_merge_suggestions (person_id, status, created_at DESC);

CREATE UNIQUE INDEX uq_identity_merge_suggestions_pending_pair
    ON identity_merge_suggestions (person_id, source_entity_id, target_entity_id)
    WHERE status = 'pending';

CREATE VIEW pending_identity_merge_suggestions AS
    SELECT * FROM identity_merge_suggestions WHERE status = 'pending';

COMMIT;
