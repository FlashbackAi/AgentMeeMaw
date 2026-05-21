"""Persistence helpers for the ``persons`` table.

Most reads/writes against ``persons`` happen inside other components
(phase gate, coverage tracker, profile summary). This module collects
the small helpers that don't fit naturally elsewhere — currently just
the row-creation path used by ``POST /persons`` during legacy
onboarding.
"""

from .repository import CreatedPerson, PersonProfile, get_person_by_id, insert_person

__all__ = ["CreatedPerson", "PersonProfile", "get_person_by_id", "insert_person"]
