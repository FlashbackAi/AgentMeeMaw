"""Sync Redis helper used by the Extraction Worker for invalidation.

The Extraction Worker is a sync process (sync Postgres pool, sync SQS,
sync Voyage). It cannot share the async ``EntityNameCache`` used by
the agent HTTP service, but it can DELETE the same key shape over a
sync redis client. Both paths keep ``entity_names:{person_id}`` as the
single source of truth.
"""

from __future__ import annotations

from uuid import UUID

from redis import Redis as SyncRedis

from flashback.entity_mention.cache import entity_name_cache_key


def invalidate_entity_name_cache(
    redis_client: SyncRedis,
    person_id: UUID,
) -> None:
    """DELETE the cached entity-name list for one person.

    Called from the Extraction Worker after entity rows commit. The
    agent's next user turn for this person reloads from Postgres on
    cache miss (see ``EntityNameCache.get``).
    """
    redis_client.delete(entity_name_cache_key(person_id))
