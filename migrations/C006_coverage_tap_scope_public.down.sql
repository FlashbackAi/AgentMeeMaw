UPDATE questions
   SET attributes = attributes - 'scope'
 WHERE source = 'coverage_tap'
   AND person_id IS NULL;
