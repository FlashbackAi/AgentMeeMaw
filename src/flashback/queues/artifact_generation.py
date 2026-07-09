"""Async producer for the ``artifact_generation`` SQS queue.

Trigger-only payload. Under the Postgres-authoritative model, the agent
writes the full generation context (prompt, negative, mode, reference
key, preset) to ``latest_generation_context`` on the originating row
*before* pushing here. The SQS message just tells the worker which row
to process; the worker fetches the context from Postgres at job time.

The same shape is used by the extraction worker's sync
:class:`flashback.workers.extraction.sqs_client.ArtifactJobSender`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from flashback.queues.client import AsyncSQSClient

log = structlog.get_logger("flashback.queues.artifact_generation")


class ArtifactGenerationQueueProducer:
    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(
        self,
        *,
        job_id: str,
        record_type: str,
        record_id: str,
        person_id: str,
        artifact_kind: str,
        source: str,
        composed_at: str,
    ) -> str | None:
        """Enqueue an artifact-generation job trigger.

        Returns the SQS MessageId, or None if no queue URL is configured
        (local dev / tests).

        ``composed_at`` must match the ``latest_generation_context.composed_at``
        the agent just wrote on the row, so the Node worker can detect and
        skip stale messages when a newer composition has superseded this one.
        """
        if not self._url:
            # In prod the artifact silently never generates — make the
            # drop unmissable in logs.
            log.error(
                "artifact_generation.enqueue_dropped_unconfigured",
                record_type=record_type,
                record_id=record_id,
                person_id=person_id,
                hint="ARTIFACT_QUEUE_URL is unset",
            )
            return None

        payload = {
            "job_id": job_id,
            "record_type": record_type,
            "record_id": record_id,
            "person_id": person_id,
            "artifact_kind": artifact_kind,
            "source": source,
            "composed_at": composed_at,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self._sqs.send_message(self._url, payload)
