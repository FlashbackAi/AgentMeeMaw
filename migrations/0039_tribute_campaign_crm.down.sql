ALTER TABLE tributes DROP COLUMN IF EXISTS campaign_id;
ALTER TABLE persons  DROP COLUMN IF EXISTS relationship_group;
-- relationship_profiles references tribute_visual_themes: drop it first.
DROP TABLE IF EXISTS tribute_campaigns;
DROP TABLE IF EXISTS relationship_profiles;
DROP TABLE IF EXISTS tribute_visual_themes;
