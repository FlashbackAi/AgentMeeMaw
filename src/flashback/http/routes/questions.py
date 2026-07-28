"""``GET /questions/feed`` — ranked producer-bank question feed.

Read-only browse surface. Ranking is agent-side computation (the API.md
§9 carve-out), so it lives here rather than as a raw Node view read.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool
from flashback.http.models import FeedQuestionOut, QuestionFeedResponse
from flashback.phase_gate.feed import DEFAULT_LIMIT, MAX_LIMIT, QuestionFeed

router = APIRouter(
    prefix="/questions",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.questions")


@router.get("/feed", response_model=QuestionFeedResponse)
async def questions_feed(
    person_id: UUID = Query(...),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> QuestionFeedResponse:
    structlog.contextvars.bind_contextvars(person_id=str(person_id))
    feed = QuestionFeed(db_pool)
    questions = await feed.build(person_id, limit=limit)
    log.info("questions.feed", count=len(questions))
    return QuestionFeedResponse(
        questions=[
            FeedQuestionOut(
                question_id=q.question_id,
                text=q.text,
                source=q.source,
                themes=q.themes,
                created_at=q.created_at,
            )
            for q in questions
        ]
    )
