"""Persist a tribute message-invitation answer before the turn pipeline.

Shared by /turn and /turn/stream. Idempotent against UI replays: an
answer arriving with no pending message tap in Working Memory is ignored
(mirrors ground_truth_answer.py). The answer is polished and written to
the tribute row -- it NEVER enters the transcript, so extraction never
mines the contributor's message (design 2026-06-14 section 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from flashback.tribute.message_llm import polish_message
from flashback.tribute.repository import set_message_async

if TYPE_CHECKING:  # avoid a circular import via flashback.http package init
    from flashback.http.models import MessageAnswerInput

log = structlog.get_logger("flashback.tribute.message_capture")


async def persist_message_answer(
    *,
    session_id: UUID,
    person_id: UUID,
    answer: MessageAnswerInput,
    wm,
    db_pool,
    settings,
) -> None:
    state = await wm.get_state(str(session_id))
    if not state.signal_pending_message:
        log.info("message_answer.ignored", reason="no_pending_message")
        return
    tribute_id = state.current_tribute_id
    if not tribute_id:
        log.info("message_answer.ignored", reason="no_tribute_id")
        await wm.clear_pending_message(str(session_id))
        return

    if answer.skipped:
        log.info("message_answer.skipped")
        await wm.clear_pending_message(str(session_id))
        return

    raw = (answer.free_text or answer.option_label or "").strip()
    if not raw:
        await wm.clear_pending_message(str(session_id))
        return

    # Look up subject name/relationship for the polish prompt.
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT name, relationship FROM persons WHERE id = %s",
                (str(person_id),),
            )
            row = await cur.fetchone()
    person_name = str(row[0]) if row else ""
    relationship = str(row[1]) if row and row[1] is not None else None

    polished = await polish_message(
        settings=settings,
        raw_text=raw,
        person_name=person_name,
        person_relationship=relationship,
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await set_message_async(
                    cur,
                    tribute_id=tribute_id,
                    message_text=polished,
                    source_turns=[{"text": raw}],
                )
    log.info("message_answer.recorded", tribute_id=tribute_id)
    await wm.clear_pending_message(str(session_id))
