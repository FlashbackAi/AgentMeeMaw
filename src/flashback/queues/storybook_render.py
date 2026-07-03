"""Async producer for the ``storybook_render`` SQS queue.

Trigger-only payload (mirrors tribute_render). The route writes the full
render context to ``storybooks.latest_generation_context['storybook']``
BEFORE pushing here; the ``storybook_render`` worker fetches that context
from Postgres at job time. Postgres is authoritative; the SQS message only
says which storybook to render.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flashback.queues.client import AsyncSQSClient


class StorybookRenderQueueProducer:
    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(
        self,
        *,
        job_id: str,
        storybook_id: str,
        person_id: str,
        composed_at: str,
    ) -> str | None:
        """Enqueue a storybook render. Returns the SQS MessageId, or None if
        no queue URL is configured (local dev / tests).

        ``composed_at`` must match the context the agent just wrote on the
        row, so the worker can skip stale messages superseded by a newer
        composition.
        """
        if not self._url:
            return None
        payload = {
            "job_id": job_id,
            "storybook_id": storybook_id,
            "person_id": person_id,
            "composed_at": composed_at,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self._sqs.send_message(self._url, payload)
