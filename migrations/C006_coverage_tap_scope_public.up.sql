UPDATE questions
   SET attributes = jsonb_set(COALESCE(attributes, '{}'::jsonb), '{scope}', '"public"', true)
 WHERE source = 'coverage_tap'
   AND person_id IS NULL
   AND COALESCE(attributes->>'scope', '') <> 'public';
