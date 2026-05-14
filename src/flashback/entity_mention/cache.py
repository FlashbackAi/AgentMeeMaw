"""Per-person entity-name cache backed by Valkey.

The cache stores the active entity catalog for one person so the
orchestrator can scan user turns for mentions without hitting Postgres
on the hot path.

Key shape: ``entity_names:{person_id}`` → JSON list of entries.

Cache lifecycle:
  * Loaded at ``/session/start`` from ``active_entities`` (Postgres).
  * Read on every user turn by the entity-mention scanner step.
  * Auto-reloads from Postgres on cache miss (rare path).
  * Invalidated (DEL) by the Extraction Worker after entity writes
    commit, so newly-extracted entities become scannable in the
    next-or-current session without waiting for TTL expiry.

Object-kind entities are excluded; matching common nouns like
"bottle" or "table" causes too many false positives for v1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

EntityKind = Literal["person", "place", "organization"]

SCANNABLE_KINDS: tuple[EntityKind, ...] = ("person", "place", "organization")


def entity_name_cache_key(person_id: UUID) -> str:
    return f"entity_names:{person_id}"


@dataclass(frozen=True, slots=True)
class EntityNameEntry:
    id: UUID
    name: str
    aliases: tuple[str, ...]
    kind: EntityKind

    def to_jsonable(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "aliases": list(self.aliases),
            "kind": self.kind,
        }

    @classmethod
    def from_jsonable(cls, raw: dict) -> "EntityNameEntry":
        return cls(
            id=UUID(raw["id"]),
            name=raw["name"],
            aliases=tuple(raw.get("aliases") or ()),
            kind=raw["kind"],
        )


class EntityNameCache:
    """Async accessor used by the agent HTTP service.

    The Extraction Worker performs cache invalidation through a sibling
    sync helper (see ``flashback.entity_mention.cache_sync``); the two
    classes do not share state beyond the key naming convention.
    """

    def __init__(
        self,
        *,
        redis_client: Redis,
        db_pool: AsyncConnectionPool,
        ttl_seconds: int,
    ) -> None:
        self._redis = redis_client
        self._pool = db_pool
        self._ttl_seconds = ttl_seconds

    async def load_from_db(self, person_id: UUID) -> list[EntityNameEntry]:
        """Read active entities for a person from Postgres."""
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, name, aliases, kind
                      FROM active_entities
                     WHERE person_id = %(person_id)s
                       AND kind IN ('person', 'place', 'organization')
                    """,
                    {"person_id": person_id},
                )
                rows = await cur.fetchall()
        return [
            EntityNameEntry(
                id=row["id"],
                name=row["name"],
                aliases=tuple(row["aliases"] or ()),
                kind=row["kind"],
            )
            for row in rows
        ]

    async def write(
        self,
        person_id: UUID,
        entries: list[EntityNameEntry],
    ) -> None:
        payload = json.dumps([e.to_jsonable() for e in entries])
        await self._redis.set(
            entity_name_cache_key(person_id),
            payload,
            ex=self._ttl_seconds,
        )

    async def refresh(self, person_id: UUID) -> list[EntityNameEntry]:
        """Read from DB and overwrite the cache entry. Returns the loaded list."""
        entries = await self.load_from_db(person_id)
        await self.write(person_id, entries)
        return entries

    async def get(self, person_id: UUID) -> list[EntityNameEntry]:
        """Return cached entries, refreshing from DB on miss."""
        raw = await self._redis.get(entity_name_cache_key(person_id))
        if raw is None:
            return await self.refresh(person_id)
        return [EntityNameEntry.from_jsonable(r) for r in json.loads(raw)]

    async def invalidate(self, person_id: UUID) -> None:
        await self._redis.delete(entity_name_cache_key(person_id))
