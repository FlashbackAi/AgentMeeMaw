"""Question scope tiers for collaborator content-scoping (SP4).

The producer LLM labels a question; the selection SQL enforces who may
be asked it. ``normalize_scope`` is the write-time coercion so every
persisted row is self-describing; the selection SQL independently
fail-safes any non-public/non-personal label to teller-only.
"""

from __future__ import annotations

PUBLIC = "public"
PERSONAL = "personal"
PRIVATE = "private"

VALID_SCOPES = frozenset({PUBLIC, PERSONAL, PRIVATE})
DEFAULT_SCOPE = PERSONAL


def normalize_scope(value: object) -> str:
    """Coerce an LLM- or user-supplied scope to a valid tier.

    Missing / empty / unknown / non-string → ``DEFAULT_SCOPE``
    (``'personal'``), the safe provenance-gated tier.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in VALID_SCOPES:
            return candidate
    return DEFAULT_SCOPE
