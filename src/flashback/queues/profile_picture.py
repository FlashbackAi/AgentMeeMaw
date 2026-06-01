"""Producer for profile-picture generation jobs.

Trigger-only payload under the Postgres-authoritative model. The agent
writes the full portrait context (prompt, negative, mode, reference
key, preset) to ``persons.latest_generation_context`` *before* pushing
here. Node's worker reads the context from Postgres at job time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from flashback.queues.client import AsyncSQSClient


class ProfilePictureQueueProducer:
    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(
        self,
        *,
        job_id: str,
        person_id: UUID,
        source: str,
        composed_at: str,
    ) -> str | None:
        """Enqueue a profile-picture job trigger.

        Returns the SQS MessageId, or None if no queue URL is configured
        (local dev / tests).

        ``composed_at`` must match ``persons.latest_generation_context.composed_at``
        so the worker can detect / skip stale messages.
        """
        if not self._url:
            return None

        payload = {
            "job_id": job_id,
            "record_type": "person",
            "record_id": str(person_id),
            "person_id": str(person_id),
            "artifact_kind": "image",
            "source": source,
            "composed_at": composed_at,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self._sqs.send_message(self._url, payload)
