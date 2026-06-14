"""Producer for post-session P2 question generation jobs."""

from __future__ import annotations

from uuid import UUID

from flashback.queues.client import AsyncSQSClient


class ProducersPerSessionQueueProducer:
    """Push one P2 producer job for a wrapped session."""

    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(
        self,
        *,
        person_id: UUID,
        session_id: UUID,
        told_by_user_id: str | None = None,
    ) -> str:
        payload = {
            "person_id": str(person_id),
            "session_id": str(session_id),
            "idempotency_key": str(session_id),
            "producer": "P2",
            "triggered_by": "session_wrap",
            "told_by_user_id": told_by_user_id or None,
        }
        return await self._sqs.send_message(self._url, payload)
