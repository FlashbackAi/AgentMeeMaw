"""Producer for profile-picture generation jobs."""

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
        mode: str,
        image_prompt: str,
        source: str,
        name: str,
        gender: str,
        relationship: str | None = None,
        reference_s3_key: str | None = None,
        user_prompt: str | None = None,
    ) -> str | None:
        """Enqueue a profile-picture job. Returns SQS MessageId or None if queue_url is empty."""
        if not self._url:
            return None

        payload = {
            "job_id": job_id,
            "user_id": str(person_id),
            "mode": mode,
            "reference_s3_key": reference_s3_key,
            "image_prompt": image_prompt,
            "negative_prompt": (
                "photorealistic, photograph, hyperrealistic, real person, deepfake, "
                "text, watermark, signature, blurry, low quality, distorted, uncanny"
            ),
            "model_hints": {
                "preset": "brand_default",
                "guidance_scale": 7.5,
                "steps": 30,
                "seed": None,
            },
            "raw_inputs": {
                "profile": {
                    "display_name": name,
                    "gender": gender,
                    "relationship": relationship,
                },
                "user_prompt": user_prompt,
            },
            "source": source,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self._sqs.send_message(self._url, payload)
