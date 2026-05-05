-- ============================================================================
-- 0008_replace_starter_questions.down.sql
-- Reverses 0008 by deleting the rewritten set and restoring the original
-- 15 starter_anchor templates from 0002_seed_starter_questions.up.sql.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Drop the rewritten templates and any edges that referenced them.
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
-- 2. Restore the original 15 (verbatim from 0002_seed_starter_questions.up.sql).
-- ----------------------------------------------------------------------------
INSERT INTO questions (text, source, attributes) VALUES

  (
    'What''s a smell that brings them right back?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "scent", "presence"]}'::jsonb
  ),
  (
    'Was there a sound — their laugh, the way they hummed, their footsteps — you''d recognize anywhere?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "sound", "presence"]}'::jsonb
  ),
  (
    'Picture them in a room you both knew well. What do you see first?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "image", "everyday"]}'::jsonb
  ),

  (
    'Was there a phrase they used so often it almost felt like their signature?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "expression", "signature"]}'::jsonb
  ),
  (
    'Was there a piece of advice they gave that stayed with you?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "advice", "wisdom"]}'::jsonb
  ),
  (
    'How would they answer the phone, or greet you when you walked in?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "greeting", "ritual"]}'::jsonb
  ),

  (
    'Where do you picture them when you think of them at their happiest?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "joy", "setting"]}'::jsonb
  ),
  (
    'Was there a place — a house, a kitchen, a porch — that felt like theirs?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "home", "belonging"]}'::jsonb
  ),
  (
    'Where would you find them on a quiet afternoon?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "daily_routine", "everyday"]}'::jsonb
  ),

  (
    'Who did they light up around?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "joy", "connection"]}'::jsonb
  ),
  (
    'How did they show people they loved them?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "love", "expression"]}'::jsonb
  ),
  (
    'When you think of the two of you together, what''s the first thing that comes to mind?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "shared_memory", "togetherness"]}'::jsonb
  ),

  (
    'If you had to pick the years that feel most like them to you, what would they be?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "life_stage", "essence"]}'::jsonb
  ),
  (
    'What was happening in their world when you knew them best?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "context", "time_period"]}'::jsonb
  ),
  (
    'When you think of them in their prime — most fully themselves — what stretch of life is that?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "life_stage", "essence"]}'::jsonb
  );

COMMIT;
