"""Shared protocol implemented by all three question producers."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from .schema import ProducerResult


class Producer(Protocol):
    """All three producers share this shape."""

    name: str
    source_tag: str

    async def produce(self, db_pool, person_id: UUID, settings) -> ProducerResult:
        """Inspect the graph, call the LLM once if needed, return questions."""
        ...

