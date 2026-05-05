"""Scalable profile facts: open-ended (question, answer) pairs about
the deceased, surfaced on the legacy profile page.

Public surface:

* :data:`SEED_FACT_KEYS` — the seven default open-tile slugs the
  frontend can show as "open" prompts when not yet filled.
* :func:`upsert_fact` — write or supersede a single fact for one
  person, push the embedding job. Used by both the profile_summary
  worker (after extraction) and the ``POST /profile_facts/upsert``
  endpoint (after a contributor edit).
* :func:`count_active_facts` — cap-counting helper.
* :data:`MAX_ACTIVE_FACTS_PER_PERSON` — hard cap (25).
"""

from __future__ import annotations

from .repository import (
    MAX_ACTIVE_FACTS_PER_PERSON,
    UpsertResult,
    count_active_facts,
    count_active_facts_async,
    upsert_fact,
    upsert_fact_async,
)
from .seeds import SEED_FACT_KEYS, SEED_FACT_QUESTIONS

__all__ = [
    "MAX_ACTIVE_FACTS_PER_PERSON",
    "SEED_FACT_KEYS",
    "SEED_FACT_QUESTIONS",
    "UpsertResult",
    "count_active_facts",
    "count_active_facts_async",
    "upsert_fact",
    "upsert_fact_async",
]
