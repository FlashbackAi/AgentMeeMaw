-- ============================================================================
-- 0011_replace_starter_questions_v2.up.sql
-- Flashback AI: Legacy Mode  -  Identity-forward starter_anchor rewrite
-- ----------------------------------------------------------------------------
-- Replaces the 0008 starter set with 15 questions that ask one open
-- question per dimension but TOUCH MULTIPLE AXES at once (era + place +
-- profession; relation + family_role; etc). Each question carries an
-- attributes.targets_fact_keys array — slugs the answer can plausibly
-- fill in profile_facts.
--
-- Coverage tracking is unchanged: each question still has a primary
-- `dimension`, so the Coverage Tracker keeps working. Multi-axis
-- targeting only changes what the extractor and profile_summary worker
-- can capture from a single response.
--
-- Five dimensions × 3 phrasings = 15 rows. Hard replace of the 0008 set.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Drop the 0008 starter_anchor templates and any edges that pointed
--    at them. Same idiom as 0008 used to drop 0002.
-- ----------------------------------------------------------------------------
DELETE FROM edges
WHERE (from_kind = 'question' AND from_id IN (
        SELECT id FROM questions
        WHERE source = 'starter_anchor' AND person_id IS NULL
      ))
   OR (to_kind = 'question' AND to_id IN (
        SELECT id FROM questions
        WHERE source = 'starter_anchor' AND person_id IS NULL
      ));

DELETE FROM questions
WHERE source = 'starter_anchor'
  AND person_id IS NULL;

-- ----------------------------------------------------------------------------
-- 2. Insert the new 15. Embeddings NULL — embedding worker backfills.
--    {name} is substituted at delivery time by StarterSelector.
-- ----------------------------------------------------------------------------
INSERT INTO questions (text, source, attributes) VALUES

  -- =====================================================================
  -- ERA  -  working life, responsibilities, time period
  -- =====================================================================
  (
    'What kind of work or responsibilities shaped most of {name}''s days?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "work", "daily_routine"],
      "targets_fact_keys": ["profession", "daily_routine", "era"]
    }'::jsonb
  ),
  (
    'What period of {name}''s life do you know best - childhood, working years, later years?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "life_stage", "identity"],
      "targets_fact_keys": ["era", "life_stage", "personality_essence"]
    }'::jsonb
  ),
  (
    'When you place {name} in time, what years or life stage matter most to their story?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "work", "historical_context"],
      "targets_fact_keys": ["era", "profession", "historical_context"]
    }'::jsonb
  ),

  -- =====================================================================
  -- PLACE  -  origin, residence, home
  -- =====================================================================
  (
    'Where was {name} from, and where did they spend most of their life?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "origin", "belonging"],
      "targets_fact_keys": ["birthplace", "residence", "place"]
    }'::jsonb
  ),
  (
    'What place should we understand first if we want to understand {name}?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "origin", "identity"],
      "targets_fact_keys": ["place", "residence", "origin"]
    }'::jsonb
  ),
  (
    'What was home like for {name} - the place, the people around them, the daily rhythm?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "home", "sense_memory"],
      "targets_fact_keys": ["home", "signature_object", "place"]
    }'::jsonb
  ),

  -- =====================================================================
  -- RELATION  -  identity, family role, people around them
  -- =====================================================================
  (
    'Who was {name} in your life, and who were they to the people around them?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "family", "community"],
      "targets_fact_keys": ["family_role", "relationships", "community"]
    }'::jsonb
  ),
  (
    'Who were the main people in {name}''s life?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "closest_person"],
      "targets_fact_keys": ["closest_person", "relationships"]
    }'::jsonb
  ),
  (
    'Who depended on {name}, and what did they depend on him for?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "role", "trust"],
      "targets_fact_keys": ["family_role", "social_role", "generosity"]
    }'::jsonb
  ),

  -- =====================================================================
  -- VOICE  -  personality, speech, temperament
  -- =====================================================================
  (
    'What was {name} like as a person?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "personality", "demeanor"],
      "targets_fact_keys": ["personality", "voice", "demeanor"]
    }'::jsonb
  ),
  (
    'How did {name} usually talk or express themselves?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "opinion", "listening"],
      "targets_fact_keys": ["voice", "personality", "listening_style"]
    }'::jsonb
  ),
  (
    'What words would someone close to {name} use to describe them?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "essence", "personality"],
      "targets_fact_keys": ["personality_essence", "voice"]
    }'::jsonb
  ),

  -- =====================================================================
  -- SENSORY  -  ordinary details, practices, objects
  -- =====================================================================
  (
    'What did {name} spend time doing when they were not working or taking care of others?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "signature", "hands"],
      "targets_fact_keys": ["signature_practice", "signature_dish", "hobby"]
    }'::jsonb
  ),
  (
    'Was there anything {name} made, taught, repaired, cooked, grew, or cared for often?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "hands", "activity"],
      "targets_fact_keys": ["hobby", "profession", "sensory_signature"]
    }'::jsonb
  ),
  (
    'What ordinary detail would help someone picture {name} in daily life?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "association", "signature"],
      "targets_fact_keys": ["sense_memory", "signature_object"]
    }'::jsonb
  );

COMMIT;
