"""Persist a tribute message-invitation answer before the turn pipeline.

Shared by /turn and /turn/stream. Idempotent against UI replays: an
answer arriving with no pending message tap in Working Memory is ignored
(mirrors ground_truth_answer.py). The answer is polished and written to
the tribute row -- it NEVER enters the transcript, so extraction never
mines the contributor's message (design 2026-06-14 section 5).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from flashback.tribute.message_llm import polish_message
from flashback.tribute.repository import set_message_async

if TYPE_CHECKING:  # avoid a circular import via flashback.http package init
    from flashback.http.models import MessageAnswerInput

log = structlog.get_logger("flashback.tribute.message_capture")


async def polish_and_store_message(
    *,
    person_id: UUID | str,
    tribute_id: str,
    raw: str,
    db_pool,
    settings,
    source: str = "chat_card",
) -> str:
    """Polish a raw contributor message and write it to the tribute row.

    Shared by the in-chat card sidecar, the typed-in-chat capture, and the
    direct tribute-card endpoint (POST /tributes/{id}/message). Returns the
    polished text.
    """
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
                    source_turns=[{"text": raw, "source": source}],
                )
    log.info("message_answer.recorded", tribute_id=tribute_id, source=source)
    return polished


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

    await polish_and_store_message(
        person_id=person_id,
        tribute_id=tribute_id,
        raw=raw,
        db_pool=db_pool,
        settings=settings,
        source="chat_card",
    )
    await wm.clear_pending_message(str(session_id))


async def maybe_capture_typed_message(
    *,
    session_id: UUID,
    person_id: UUID,
    user_message: str,
    wm,
    db_pool,
    settings,
) -> bool:
    """Catch a message typed as a normal chat reply after the card was offered.

    One-shot: only the FIRST user turn after an invitation is checked (the
    ``signal_message_typed_check`` flag arms on emit and is consumed here),
    so the classifier never runs on every turn. Conservative: only a clear
    "this reply IS the message" verdict captures; anything else leaves the
    pending card armed and the turn untouched. Returns True when captured.
    """
    from flashback.tribute.typed_message import is_direct_message

    state = await wm.get_state(str(session_id))
    if not state.signal_message_typed_check or not state.signal_pending_message:
        return False
    # Consume the one-shot regardless of outcome.
    await wm.update_signals(str(session_id), signal_message_typed_check="")

    tribute_id = state.current_tribute_id
    text = (user_message or "").strip()
    if not tribute_id or len(text) < 8:
        return False

    invitation_copy = ""
    try:
        invitation_copy = str(
            json.loads(state.signal_pending_message).get("text") or ""
        )
    except (ValueError, TypeError, AttributeError):
        pass

    try:
        is_message = await is_direct_message(
            settings, invitation_copy=invitation_copy, user_reply=text
        )
    except Exception:
        log.warning("typed_message.classify_failed", exc_info=True)
        return False
    if not is_message:
        log.info("typed_message.not_a_message")
        return False

    await polish_and_store_message(
        person_id=person_id,
        tribute_id=tribute_id,
        raw=text,
        db_pool=db_pool,
        settings=settings,
        source="chat_typed",
    )
    await wm.clear_pending_message(str(session_id))
    return True
