"""Producer for post-session Profile Summary jobs."""

from __future__ import annotations

from uuid import UUID

from flashback.queues.client import AsyncSQSClient


class ProfileSummaryQueueProducer:
    """Push one profile-summary regeneration job for a wrapped session."""

    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(self, *, person_id: UUID, session_id: UUID) -> str:
        payload = {
            "person_id": str(person_id),
            "session_id": str(session_id),
            "idempotency_key": str(session_id),
            "triggered_by": "session_wrap",
        }
        return await self._sqs.send_message(self._url, payload)
