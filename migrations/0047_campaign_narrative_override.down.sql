-- 0047_campaign_narrative_override.down.sql  -  reverse 0047

BEGIN;

ALTER TABLE tribute_campaigns
    DROP COLUMN IF EXISTS narrative_override;

COMMIT;
