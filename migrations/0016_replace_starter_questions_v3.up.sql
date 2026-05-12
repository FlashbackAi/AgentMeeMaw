-- ============================================================================
-- 0016_replace_starter_questions_v3.up.sql
-- Flashback AI: Legacy Mode  -  Short & grounded starter_anchor rewrite
-- ----------------------------------------------------------------------------
-- Replaces the 0011 starter set with 15 questions written to sound like a
-- friend at a kitchen table rather than an interviewer. Strips triplet
-- lists, six-verb chains, and meta-framing. Introduces pronoun
-- placeholders ({they}, {them}, {their}) that StarterSelector substitutes
-- from persons.gender, alongside the existing {name} placeholder.
--
-- Coverage tracking unchanged: each question still has a primary
-- `dimension`, so the Coverage Tracker keeps working. Five dimensions x
-- 3 phrasings = 15 rows. Hard replace of the 0011 set.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Drop the 0011 starter_anchor templates and any edges that pointed
--    at them. Same idiom as 0011 used to drop 0008.
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
--    {name} / {they} / {them} / {their} are substituted at delivery time
--    by StarterSelector.
-- ----------------------------------------------------------------------------
INSERT INTO questions (text, source, attributes) VALUES

  -- =====================================================================
  -- ERA  -  work, life period, time
  -- =====================================================================
  (
    'What did {name} do for work?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "work", "daily_routine"],
      "targets_fact_keys": ["profession", "daily_routine", "era"]
    }'::jsonb
  ),
  (
    'What part of {their} life do you know best?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "life_stage", "identity"],
      "targets_fact_keys": ["era", "life_stage", "personality_essence"]
    }'::jsonb
  ),
  (
    'What years are we talking about, roughly?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "historical_context"],
      "targets_fact_keys": ["era", "historical_context"]
    }'::jsonb
  ),

  -- =====================================================================
  -- PLACE  -  origin, residence, home
  -- =====================================================================
  (
    'Where was {name} from?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "origin"],
      "targets_fact_keys": ["birthplace", "origin", "place"]
    }'::jsonb
  ),
  (
    'Where did {name} live most of {their} life?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "residence"],
      "targets_fact_keys": ["residence", "place"]
    }'::jsonb
  ),
  (
    'What was home like for {them}?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "home", "sense_memory"],
      "targets_fact_keys": ["home", "signature_object", "place"]
    }'::jsonb
  ),

  -- =====================================================================
  -- RELATION  -  family role, people around them
  -- =====================================================================
  (
    'Who was {name} to you?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "family"],
      "targets_fact_keys": ["family_role", "relationships"]
    }'::jsonb
  ),
  (
    'Who were the people closest to {name}?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "closest_person"],
      "targets_fact_keys": ["closest_person", "relationships"]
    }'::jsonb
  ),
  (
    'Who did {name} take care of?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "role", "care"],
      "targets_fact_keys": ["family_role", "social_role", "generosity"]
    }'::jsonb
  ),

  -- =====================================================================
  -- VOICE  -  personality, speech, temperament
  -- =====================================================================
  (
    'What was {name} like?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "personality"],
      "targets_fact_keys": ["personality", "voice", "personality_essence"]
    }'::jsonb
  ),
  (
    'What was {name} like to talk to?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "demeanor"],
      "targets_fact_keys": ["voice", "demeanor", "listening_style"]
    }'::jsonb
  ),
  (
    'What''s the first word that comes to mind when you think of {name}?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "essence"],
      "targets_fact_keys": ["personality_essence", "voice"]
    }'::jsonb
  ),

  -- =====================================================================
  -- SENSORY  -  scene, association, signature object
  -- =====================================================================
  (
    'When you picture {name}, what do you see?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "scene"],
      "targets_fact_keys": ["sense_memory", "signature_object"]
    }'::jsonb
  ),
  (
    'What''s something small that reminds you of {name}?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "association"],
      "targets_fact_keys": ["sense_memory", "signature_object", "sensory_signature"]
    }'::jsonb
  ),
  (
    'What''s something {name} always had with {them}?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "signature"],
      "targets_fact_keys": ["signature_object", "sensory_signature"]
    }'::jsonb
  );

COMMIT;
