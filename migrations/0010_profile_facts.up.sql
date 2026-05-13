-- ============================================================================
-- 0010_profile_facts.up.sql
-- Flashback AI: Legacy Mode  -  Scalable profile facts (open-ended Q+A pairs)
-- ----------------------------------------------------------------------------
-- A profile fact is a single (question, answer) pair about the legacy subject,
-- e.g. ("What did Maria do for a living?", "Farmer"). Facts are surfaced
-- on the legacy profile page (Node owns the UI) and are editable by the
-- contributor.
--
-- Design notes:
--
-- * fact_key is a slug picked by the extraction LLM (e.g. 'profession',
--   'birthplace', 'instruments_played'). The seven seed slugs in
--   src/flashback/profile_facts/seeds.py act as default open-tile
--   prompts; new keys grow organically as the conversation reveals more.
--
-- * Edits supersede via status flip + new row, matching the canonical
--   graph supersession invariant. status = 'active' | 'superseded'.
--
-- * Cap: 25 active rows per person, enforced in app code (not a DB
--   constraint, since the cap may evolve).
--
-- * answer_embedding follows the same triple-column pattern as moments /
--   entities / etc. The embedding worker is the only writer of the
--   vector + model identity columns (CLAUDE.md invariant #4).
-- ============================================================================

BEGIN;

CREATE TABLE profile_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,

    fact_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,

    -- Provenance: where this fact came from.
    --   'starter_extraction'  - extracted by the profile_summary worker
    --                           from active moments during a session wrap
    --   'user_edit'           - upserted via POST /profile_facts/upsert
    source TEXT NOT NULL CHECK (source IN ('starter_extraction', 'user_edit')),

    -- Status / supersession (mirrors moments / entities / threads).
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded')),
    superseded_by UUID REFERENCES profile_facts(id),

    -- Embedding (filled by the embedding worker).
    answer_embedding vector(1024),
    embedding_model TEXT,
    embedding_model_version TEXT,

    -- Audit
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_profile_facts_embedding_complete CHECK (
        (answer_embedding IS NULL
            AND embedding_model IS NULL
            AND embedding_model_version IS NULL)
        OR
        (answer_embedding IS NOT NULL
            AND embedding_model IS NOT NULL
            AND embedding_model_version IS NOT NULL)
    )
);

CREATE TRIGGER trg_profile_facts_updated_at BEFORE UPDATE ON profile_facts
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- One active fact per (person, key). New answers supersede the prior
-- active row inside a transaction, then the new active row is inserted.
CREATE UNIQUE INDEX uq_profile_facts_active_key
    ON profile_facts (person_id, fact_key)
    WHERE status = 'active';

-- Cap-counting + listing per person.
CREATE INDEX idx_profile_facts_person_status
    ON profile_facts (person_id, status);

-- Active view, in line with active_moments / active_entities / etc.
CREATE VIEW active_profile_facts AS
    SELECT * FROM profile_facts WHERE status = 'active';

COMMIT;
