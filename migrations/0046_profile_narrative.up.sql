-- ============================================================================
-- 0046_profile_narrative.up.sql
-- Flashback AI: Legacy Mode  -  relationship-profile narrative framing
-- ----------------------------------------------------------------------------
-- The tribute-video assembler had a hard-coded Father's Day skeleton: it always
-- framed the book as "tell a stranger the arc of this person's LIFE, spotlight
-- on THEM, ordered early-life -> work -> family -> late years." For a Friendship
-- Day flashback (two living friends, shared jokes, no life arc) that framing is
-- wrong and the narrative reads like an obituary.
--
-- This adds a `narrative` directive to the relationship profile (the tone
-- owner, alongside voice/opener/art) so the FRAMING is authored per relationship
-- in the CRM, not coded per occasion:
--
--   narrative = {
--     "audience":    who the flashback speaks to / who is watching,
--     "arc":         how to order the pages (the shape of the story),
--     "throughline": what the whole piece is ultimately about
--   }
--
-- All keys optional; an empty {} reproduces the code-side memorial default at
-- compose time, so existing profiles and pre-0046 snapshots render identically.
-- ============================================================================

BEGIN;

ALTER TABLE relationship_profiles
    ADD COLUMN IF NOT EXISTS narrative JSONB NOT NULL DEFAULT '{}';

COMMIT;
