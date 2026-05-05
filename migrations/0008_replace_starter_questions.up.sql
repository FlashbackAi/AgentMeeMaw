-- ============================================================================
-- 0008_replace_starter_questions.up.sql
-- Flashback AI: Legacy Mode  -  Replace 0002 starter_anchor seed
-- ----------------------------------------------------------------------------
-- Replaces the original 15 starter_anchor templates with a rewritten set
-- that uses the {name} placeholder. The phase_gate StarterSelector
-- substitutes {name} with persons.name at delivery time.
--
-- Tone shift: less grief-counselor, more story-gatherer. Each dimension
-- still gets 3 phrasings (5 dimensions × 3 = 15).
--
-- Idempotency: this is a hard replace. Old rows are deleted (along with
-- any edges referencing them) before the new set is inserted.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Drop old starter_anchor templates and any edges that referenced them.
--    Same pattern as 0002_seed_starter_questions.down.sql.
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
-- 2. Seed the new 15. Embeddings are NULL; the embedding worker backfills.
-- ----------------------------------------------------------------------------
INSERT INTO questions (text, source, attributes) VALUES

  -- =====================================================================
  -- SENSORY  -  what their hands did, what filled their space
  -- =====================================================================
  (
    'What did {name} do with their hands — were they always cooking, fixing something, making things?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "hands", "activity"]}'::jsonb
  ),
  (
    'Was there a dish {name} made that people still talk about?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "food", "signature"]}'::jsonb
  ),
  (
    'What did {name}''s place look like — was it neat, full of things, always something going on?',
    'starter_anchor',
    '{"dimension": "sensory", "themes": ["sense_memory", "home", "setting"]}'::jsonb
  ),

  -- =====================================================================
  -- VOICE  -  how they said things, the texture of their opinions
  -- =====================================================================
  (
    'What kind of thing would {name} say when they had an opinion about something?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "opinion", "expression"]}'::jsonb
  ),
  (
    'Did {name} have a way of putting things that was just... theirs?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "expression", "signature"]}'::jsonb
  ),
  (
    'Was {name} the type to give advice, or more the type to just listen?',
    'starter_anchor',
    '{"dimension": "voice", "themes": ["voice", "advice", "listening"]}'::jsonb
  ),

  -- =====================================================================
  -- PLACE  -  where they spent time, where they came from
  -- =====================================================================
  (
    'Where did {name} spend most of their time — was there a room or a spot that was really theirs?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "home", "belonging"]}'::jsonb
  ),
  (
    'Where did {name} grow up, and did it stick with them?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "origin", "history"]}'::jsonb
  ),
  (
    'Is there somewhere you associate with {name} more than anywhere else?',
    'starter_anchor',
    '{"dimension": "place", "themes": ["place", "shared_memory", "association"]}'::jsonb
  ),

  -- =====================================================================
  -- RELATION  -  their role with people, who they were "for"
  -- =====================================================================
  (
    'What was {name}''s role in the family — were they the one who kept everyone together?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "family", "role"]}'::jsonb
  ),
  (
    'Who were the people {name} talked about most?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "connection", "social"]}'::jsonb
  ),
  (
    'What did people come to {name} for?',
    'starter_anchor',
    '{"dimension": "relation", "themes": ["relationship", "role", "trust"]}'::jsonb
  ),

  -- =====================================================================
  -- ERA  -  what filled their life, the shape of an ordinary week
  -- =====================================================================
  (
    'What was {name} doing for most of their life — work, a trade, raising people?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "work", "life_stage"]}'::jsonb
  ),
  (
    'Is there a time in {name}''s life you feel like you know really well — and one you don''t?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "life_stage", "memory_gaps"]}'::jsonb
  ),
  (
    'What did a regular week look like for {name}?',
    'starter_anchor',
    '{"dimension": "era", "themes": ["era", "daily_routine", "everyday"]}'::jsonb
  );

COMMIT;
