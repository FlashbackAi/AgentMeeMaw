-- ============================================================================
-- 0021_question_decisions.up.sql
-- Flashback AI: Legacy Mode - Question decisions
-- ----------------------------------------------------------------------------
-- Captures explicit user decisions on producer-bank questions surfaced via
-- the chip surface (Skip / Don't ask again / I'll tell you later).
--
-- One active row per (question_id, person_id); supersession via status flip.
-- Read path: phase_gate eligibility queries do NOT EXISTS against this table
-- so suppressed questions are permanently excluded and skipped questions are
-- excluded with a 3-step fallback when the bank would otherwise be empty.
-- ============================================================================

CREATE TABLE question_decisions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id   uuid NOT NULL REFERENCES questions(id),
    person_id     uuid NOT NULL REFERENCES persons(id),
    action        text NOT NULL CHECK (action IN ('skip', 'suppress', 'defer')),
    decided_at    timestamptz NOT NULL DEFAULT now(),
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    superseded_by uuid REFERENCES question_decisions(id)
);

CREATE UNIQUE INDEX idx_question_decisions_active
    ON question_decisions (question_id, person_id)
    WHERE status = 'active';

CREATE INDEX idx_question_decisions_lookup
    ON question_decisions (person_id, action, decided_at)
    WHERE status = 'active';

CREATE VIEW active_question_decisions AS
    SELECT *
    FROM question_decisions
    WHERE status = 'active';
