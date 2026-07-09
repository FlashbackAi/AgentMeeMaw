"""Async producer for the ``tribute_render`` SQS queue.

Trigger-only payload (mirrors artifact_generation). The agent assembles the
Book + writes the full render context (Book, presigned URLs, subject info) to
``tributes.latest_generation_context['tribute_video']`` BEFORE pushing here; the
``tribute_render`` worker fetches that context from Postgres at job time. Postgres
is authoritative; the SQS message only says which tribute to render.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from flashback.queues.client import AsyncSQSClient

log = structlog.get_logger("flashback.queues.tribute_render")


class TributeRenderQueueProducer:
    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(
        self,
        *,
        job_id: str,
        tribute_id: str,
        person_id: str,
        composed_at: str,
    ) -> str | None:
        """Enqueue a tribute render. Returns the SQS MessageId, or None if no
        queue URL is configured (local dev / tests).

        ``composed_at`` must match the context the agent just wrote on the row,
        so the worker can skip stale messages superseded by a newer composition.
        """
        if not self._url:
            # In prod this strands the tribute at status='generating' forever
            # (no message, empty DLQ) — make the drop unmissable in logs.
            log.error(
                "tribute_render.enqueue_dropped_unconfigured",
                tribute_id=tribute_id,
                person_id=person_id,
                hint="TRIBUTE_RENDER_QUEUE_URL is unset",
            )
            return None
        payload = {
            "job_id": job_id,
            "tribute_id": tribute_id,
            "person_id": person_id,
            "composed_at": composed_at,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self._sqs.send_message(self._url, payload)
