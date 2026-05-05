"""Seed slot fact_keys + canonical question phrasings.

These seven keys are NOT a hard registry — the table accepts any
fact_key the extraction LLM proposes. They exist to:

1. Give the frontend a default set of "open" tiles to display when a
   person's profile is sparse.
2. Provide canonical question phrasings the LLM can use when an
   extracted answer fits one of these well-known facts.

New keys grow organically as the conversation reveals more about the
deceased (e.g. ``signature_dish``, ``instruments_played``,
``military_service``). Salience and the per-person cap (see
:data:`flashback.profile_facts.MAX_ACTIVE_FACTS_PER_PERSON`) keep the
profile from growing without bound.
"""

from __future__ import annotations

# Order is meaningful: it's the default display order on the legacy
# profile when these are still open tiles.
SEED_FACT_KEYS: tuple[str, ...] = (
    "profession",
    "birthplace",
    "residence",
    "faith",
    "family_role",
    "era",
    "personality_essence",
)

# Canonical question phrasings — used when the extractor fills a seed
# slug for the first time and didn't propose its own question text. The
# ``{name}`` placeholder is substituted at write time.
SEED_FACT_QUESTIONS: dict[str, str] = {
    "profession": "What did {name} do for a living?",
    "birthplace": "Where was {name} born?",
    "residence": "Where did {name} live most of their life?",
    "faith": "Did {name} practice a faith or have spiritual beliefs?",
    "family_role": "What was {name}'s role in the family?",
    "era": "Roughly when did {name} live?",
    "personality_essence": "If you had to capture {name} in a word, what would it be?",
}
