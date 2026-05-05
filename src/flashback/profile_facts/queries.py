"""SQL constants for profile_facts read/write operations."""

from __future__ import annotations

COUNT_ACTIVE_FACTS = """
SELECT count(*)
FROM active_profile_facts
WHERE person_id = %(person_id)s
"""

SELECT_ACTIVE_FACT_BY_KEY = """
SELECT id, question_text, answer_text, source
FROM active_profile_facts
WHERE person_id = %(person_id)s
  AND fact_key  = %(fact_key)s
"""

SUPERSEDE_ACTIVE_FACT = """
UPDATE profile_facts
   SET status        = 'superseded',
       superseded_by = %(superseded_by)s,
       updated_at    = now()
 WHERE id            = %(id)s
   AND status        = 'active'
"""

INSERT_FACT = """
INSERT INTO profile_facts (
    id, person_id, fact_key, question_text, answer_text, source, status
) VALUES (
    %(id)s, %(person_id)s, %(fact_key)s,
    %(question_text)s, %(answer_text)s, %(source)s, 'active'
)
RETURNING id
"""
