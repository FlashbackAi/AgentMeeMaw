"""Deterministic entity-mention scanning.

A pre-retrieval surface, independent of intent. When the user mentions
a known entity by name (or alias) inside any utterance, the scanner
fetches that entity from the cached graph and injects it into the
response generator's context so the agent can talk about it correctly.

This is separate from the semantic vector retrieval matrix — that
matrix governs ``search_moments`` / ``search_entities`` calls based on
intent. Entity mention scanning runs every turn regardless of intent,
costs zero Voyage calls, and answers the most common real-world
pattern: contributors mentioning known entities mid-narrative.
"""

from flashback.entity_mention.cache import (
    EntityNameCache,
    EntityNameEntry,
    entity_name_cache_key,
)
from flashback.entity_mention.matcher import EntityMatch, find_entity_mentions

__all__ = [
    "EntityMatch",
    "EntityNameCache",
    "EntityNameEntry",
    "entity_name_cache_key",
    "find_entity_mentions",
]
