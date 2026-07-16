-- ============================================================================
-- 0041_campaign_relationship_targeting.up.sql
-- Flashback AI: Legacy Mode  -  campaigns can target relationship groups
-- ----------------------------------------------------------------------------
-- Ask (2026-07-16): "I don't want some campaigns to be in all the profiles."
-- A featured campaign used to surface on EVERY legacy (a father profile got
-- the Friendship Day card, and the campaign's question-bank override even
-- replaced the parent profile's questions).
--
-- relationship_groups lists the profile group slugs the campaign applies to;
-- empty = all relationships (pre-0041 behavior, the default). Enforced in
-- code via campaign_applies() at every surface that resolves a campaign for
-- a person (unlock_prepare, session-start stamping, render config, the
-- public campaign list).
-- ============================================================================

BEGIN;

ALTER TABLE tribute_campaigns
    ADD COLUMN relationship_groups TEXT[] NOT NULL DEFAULT '{}';

COMMIT;
