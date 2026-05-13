-- ============================================================================
-- Revert 0017 archetype onboarding columns and starter anchors.
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.person_roles') IS NOT NULL THEN
        ALTER TABLE person_roles
            DROP COLUMN IF EXISTS archetype_answers,
            DROP COLUMN IF EXISTS onboarding_complete;
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
  (
    'What did {name} do for work?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "work", "daily_routine"], "targets_fact_keys": ["profession", "daily_routine", "era"]}'::jsonb
  ),
  (
    'What part of {their} life do you know best?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "life_stage", "identity"], "targets_fact_keys": ["era", "life_stage", "personality_essence"]}'::jsonb
  ),
  (
    'What years are we talking about, roughly?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "historical_context"], "targets_fact_keys": ["era", "historical_context"]}'::jsonb
  ),
  (
    'Where was {name} from?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "origin"], "targets_fact_keys": ["birthplace", "origin", "place"]}'::jsonb
  ),
  (
    'Where did {name} live most of {their} life?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "residence"], "targets_fact_keys": ["residence", "place"]}'::jsonb
  ),
  (
    'What was home like for {them}?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "home", "sense_memory"], "targets_fact_keys": ["home", "signature_object", "place"]}'::jsonb
  ),
  (
    'Who was {name} to you?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "family"], "targets_fact_keys": ["family_role", "relationships"]}'::jsonb
  ),
  (
    'Who were the people closest to {name}?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "closest_person"], "targets_fact_keys": ["closest_person", "relationships"]}'::jsonb
  ),
  (
    'Who did {name} take care of?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "role", "care"], "targets_fact_keys": ["family_role", "social_role", "generosity"]}'::jsonb
  ),
  (
    'What was {name} like?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "personality"], "targets_fact_keys": ["personality", "voice", "personality_essence"]}'::jsonb
  ),
  (
    'What was {name} like to talk to?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "demeanor"], "targets_fact_keys": ["voice", "demeanor", "listening_style"]}'::jsonb
  ),
  (
    'What''s the first word that comes to mind when you think of {name}?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "essence"], "targets_fact_keys": ["personality_essence", "voice"]}'::jsonb
  ),
  (
    'When you picture {name}, what do you see?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "scene"], "targets_fact_keys": ["sense_memory", "signature_object"]}'::jsonb
  ),
  (
    'What''s something small that reminds you of {name}?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "association"], "targets_fact_keys": ["sense_memory", "signature_object", "sensory_signature"]}'::jsonb
  ),
  (
    'What''s something {name} always had with {them}?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "signature"], "targets_fact_keys": ["signature_object", "sensory_signature"]}'::jsonb
  );

COMMIT;
