"""Literal SQL for the collaborator_onboarding mirror table."""

# Upsert the active onboarding row for (person_id, user_id). On conflict
# (the partial-unique active row), refresh modal timestamps and ONLY set the
# voice anchor when a new non-NULL one is supplied (never clobber a captured
# anchor with an empty re-mirror). COALESCE keeps the existing value.
UPSERT_ONBOARDING_SQL = """
INSERT INTO collaborator_onboarding (
    person_id, user_id,
    voice_anchor_text, voice_anchored_at,
    modal_answered_at, modal_dismissed_at
)
VALUES (
    %(person_id)s, %(user_id)s,
    %(voice_anchor_text)s, %(voice_anchored_at)s,
    %(modal_answered_at)s, %(modal_dismissed_at)s
)
ON CONFLICT (person_id, user_id) WHERE status = 'active'
DO UPDATE SET
    voice_anchor_text = COALESCE(EXCLUDED.voice_anchor_text, collaborator_onboarding.voice_anchor_text),
    voice_anchored_at = COALESCE(EXCLUDED.voice_anchored_at, collaborator_onboarding.voice_anchored_at),
    modal_answered_at = COALESCE(EXCLUDED.modal_answered_at, collaborator_onboarding.modal_answered_at),
    modal_dismissed_at = COALESCE(EXCLUDED.modal_dismissed_at, collaborator_onboarding.modal_dismissed_at)
"""

GET_VOICE_ANCHOR_SQL = """
SELECT voice_anchor_text
FROM collaborator_onboarding
WHERE person_id = %(person_id)s
  AND user_id = %(user_id)s
  AND status = 'active'
"""
