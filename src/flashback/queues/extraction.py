"""Producer for extraction segment jobs."""

from __future__ import annotations

from uuid import UUID

from flashback.queues.client import AsyncSQSClient
from flashback.working_memory import Turn


class ExtractionQueueProducer:
    """Push closed segment jobs to the extraction queue."""

    def __init__(self, sqs_client: AsyncSQSClient, queue_url: str):
        self._sqs = sqs_client
        self._url = queue_url

    async def push(
        self,
        *,
        session_id: UUID,
        person_id: UUID,
        segment_turns: list[Turn],
        rolling_summary: str,
        prior_rolling_summary: str,
        seeded_question_id: UUID | None,
        contributor_display_name: str = "",
    ) -> str:
        """Push an extraction job and return the SQS MessageId."""

        payload = {
            "session_id": str(session_id),
            "person_id": str(person_id),
            "segment_turns": [
                turn.model_dump(mode="json") for turn in segment_turns
            ],
            "rolling_summary": rolling_summary,
            "prior_rolling_summary": prior_rolling_summary,
            "seeded_question_id": (
                str(seeded_question_id) if seeded_question_id else None
            ),
            "contributor_display_name": contributor_display_name or "",
        }
        return await self._sqs.send_message(self._url, payload)
