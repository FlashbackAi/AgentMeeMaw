"""Literal SQL for the collaborator_onboarding mirror table."""

# Upsert the active onboarding row for (person_id, user_id). On conflict
# (the partial-unique active row), refresh modal timestamps and ONLY set the
# voice anchor when a new non-NULL one is supplied (never clobber a captured
# anchor with an empty re-mirror). COALESCE keeps the existing value.
UPSERT_ONBOARDING_SQL = """
INSERT INTO collaborator_onboarding (
    person_id, user_id,
    voice_anchor_text, voice_anchored_at,
    modal_answered_at, modal_dismissed_at,
    display_name
)
VALUES (
    %(person_id)s, %(user_id)s,
    %(voice_anchor_text)s, %(voice_anchored_at)s,
    %(modal_answered_at)s, %(modal_dismissed_at)s,
    %(display_name)s
)
ON CONFLICT (person_id, user_id) WHERE status = 'active'
DO UPDATE SET
    voice_anchor_text = COALESCE(EXCLUDED.voice_anchor_text, collaborator_onboarding.voice_anchor_text),
    voice_anchored_at = COALESCE(EXCLUDED.voice_anchored_at, collaborator_onboarding.voice_anchored_at),
    modal_answered_at = COALESCE(EXCLUDED.modal_answered_at, collaborator_onboarding.modal_answered_at),
    modal_dismissed_at = COALESCE(EXCLUDED.modal_dismissed_at, collaborator_onboarding.modal_dismissed_at),
    display_name = COALESCE(EXCLUDED.display_name, collaborator_onboarding.display_name)
"""

GET_VOICE_ANCHOR_SQL = """
SELECT voice_anchor_text
FROM collaborator_onboarding
WHERE person_id = %(person_id)s
  AND user_id = %(user_id)s
  AND status = 'active'
"""

GET_ONBOARDING_STATE_SQL = """
SELECT phase,
       (first_moment_id IS NOT NULL) AS has_memory,
       (voice_anchor_text IS NOT NULL
        OR modal_answered_at IS NOT NULL
        OR modal_dismissed_at IS NOT NULL) AS has_connection,
       taps_emitted
FROM collaborator_onboarding
WHERE person_id = %(person_id)s
  AND user_id   = %(user_id)s
  AND status    = 'active'
"""

# Executed with a SYNC cursor by the Extraction Worker tx-tail (no async
# wrapper by design): mark the collaborator's first moment + fill the voice
# anchor from an inferred relationship (non-clobber). Async callers use the
# helpers in repository.py instead.
MARK_FIRST_MOMENT_SQL = """
UPDATE collaborator_onboarding
   SET first_moment_id          = %(moment_id)s,
       first_moment_recorded_at = now()
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
   AND first_moment_id IS NULL
"""

SET_VOICE_ANCHOR_IF_EMPTY_SQL = """
UPDATE collaborator_onboarding
   SET voice_anchor_text = %(voice_anchor_text)s,
       voice_anchored_at = now()
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
   AND voice_anchor_text IS NULL
"""

FLIP_PHASE_IF_COMPLETE_SQL = """
UPDATE collaborator_onboarding
   SET phase = 'active', phase_locked_at = now()
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
   AND phase     = 'onboarding'
   AND first_moment_id IS NOT NULL
   AND (voice_anchor_text IS NOT NULL
        OR modal_answered_at IS NOT NULL
        OR modal_dismissed_at IS NOT NULL)
"""

INCREMENT_TAPS_EMITTED_SQL = """
UPDATE collaborator_onboarding
   SET taps_emitted = taps_emitted + 1
 WHERE person_id = %(person_id)s
   AND user_id   = %(user_id)s
   AND status    = 'active'
"""
