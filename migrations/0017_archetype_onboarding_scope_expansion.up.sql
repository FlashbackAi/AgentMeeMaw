-- ============================================================================
-- 0017_archetype_onboarding_scope_expansion.up.sql
-- Archetype onboarding + status-neutral, scene-anchored starter anchors.
-- ----------------------------------------------------------------------------
-- person_roles is owned by the Node backend. In environments where that table
-- is present in the shared Postgres database, add the onboarding columns the
-- agent contract now expects. Local agent-only test databases do not create
-- person_roles, so this migration is intentionally guarded.
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.person_roles') IS NOT NULL THEN
        ALTER TABLE person_roles
            ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS archetype_answers JSONB NOT NULL DEFAULT '[]'::jsonb;
    END IF;
END;
$$;

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

INSERT INTO questions (text, source, attributes) VALUES

  -- ERA (target: time_anchor with year OR life_period_estimate)
  (
    'Which part of {name}''s life do you know best?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "life_stage"],
      "targets_fact_keys": ["era", "life_stage", "personality_essence"]
    }'::jsonb
  ),
  (
    'How old was {name} when most of what you know about {them} happened?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "age", "life_stage"],
      "targets_fact_keys": ["era", "life_stage"]
    }'::jsonb
  ),
  (
    'Which years of {their} life feel clearest to you?',
    'starter_anchor',
    '{
      "dimension": "era",
      "themes": ["era", "historical_context"],
      "targets_fact_keys": ["era", "historical_context"]
    }'::jsonb
  ),

  -- PLACE (target: involves edge to place entity)
  (
    'Where would you usually find {name}?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "residence", "daily_routine"],
      "targets_fact_keys": ["residence", "place", "daily_routine"]
    }'::jsonb
  ),
  (
    'What place comes to mind first when you think of {name}?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "association"],
      "targets_fact_keys": ["place", "signature_object"]
    }'::jsonb
  ),
  (
    'Where does {name} spend most of {their} days?',
    'starter_anchor',
    '{
      "dimension": "place",
      "themes": ["place", "home", "work"],
      "targets_fact_keys": ["home", "residence", "place"]
    }'::jsonb
  ),

  -- RELATION (target: involves edge to non-subject person entity)
  (
    'Who else was usually around when you spent time with {name}?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "co_witness"],
      "targets_fact_keys": ["relationships", "closest_person"]
    }'::jsonb
  ),
  (
    'Who was {name} closest to?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "closest_person"],
      "targets_fact_keys": ["closest_person", "relationships"]
    }'::jsonb
  ),
  (
    'Whose name comes up the most when {name} is talked about?',
    'starter_anchor',
    '{
      "dimension": "relation",
      "themes": ["relationship", "people_in_orbit"],
      "targets_fact_keys": ["relationships", "closest_person"]
    }'::jsonb
  ),

  -- VOICE (target: trait extraction OR entity with saying/mannerism attribute)
  (
    'Is there a phrase or word {name} always says?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "saying", "phrase"],
      "targets_fact_keys": ["voice", "memorable_phrases", "personality_essence"]
    }'::jsonb
  ),
  (
    'How does {name} usually start a conversation?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "mannerism", "demeanor"],
      "targets_fact_keys": ["voice", "demeanor", "listening_style"]
    }'::jsonb
  ),
  (
    'Is there a way {name} talks — a tone, a habit, something specific — that stands out?',
    'starter_anchor',
    '{
      "dimension": "voice",
      "themes": ["voice", "mannerism"],
      "targets_fact_keys": ["voice", "personality_essence", "demeanor"]
    }'::jsonb
  ),

  -- SENSORY (target: sensory_details non-empty on moment)
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
    'What small thing brings {name} to mind?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "association"],
      "targets_fact_keys": ["sense_memory", "signature_object", "sensory_signature"]
    }'::jsonb
  ),
  (
    'What is something {name} always has with {them}?',
    'starter_anchor',
    '{
      "dimension": "sensory",
      "themes": ["sense_memory", "signature"],
      "targets_fact_keys": ["signature_object", "sensory_signature"]
    }'::jsonb
  );

COMMIT;