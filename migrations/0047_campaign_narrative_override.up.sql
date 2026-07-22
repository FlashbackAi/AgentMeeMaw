-- ============================================================================
-- 0047_campaign_narrative_override.up.sql
-- Flashback AI: Legacy Mode  -  campaign-level narrative override
-- ----------------------------------------------------------------------------
-- 0046 put narrative framing (audience/arc/throughline) on the relationship
-- profile. But a video is generated under an OCCASION campaign (Friendship
-- Day), and the occasion is what should decide the framing -- a Friendship Day
-- flashback is a friendship celebration regardless of which relationship it
-- targets. So the campaign gets an override that wins over the profile default,
-- exactly like archetype_bank_override / visual_theme_id / message_card_copy:
--
--   compose:  narrative = campaign.narrative_override or profile.narrative
--
-- Empty {} = inherit the relationship profile's narrative (which itself falls
-- back to the memorial default when empty). So the resolution never comes back
-- empty and pre-0047 campaigns behave exactly as before.
-- ============================================================================

BEGIN;

ALTER TABLE tribute_campaigns
    ADD COLUMN IF NOT EXISTS narrative_override JSONB NOT NULL DEFAULT '{}';

COMMIT;
