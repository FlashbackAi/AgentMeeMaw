"""Persist a ground-truth tap answer before the turn pipeline runs.

Shared by /turn and /turn/stream. Idempotent against UI replays: an
answer arriving with no pending GT tap in Working Memory is ignored
(design 2026-06-11 §7)."""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from flashback.ground_truth.store import upsert_ground_truth_field
from flashback.http.models import GroundTruthAnswerInput

log = structlog.get_logger("flashback.http.ground_truth")


async def persist_ground_truth_answer(
    *,
    session_id: UUID,
    person_id: UUID,
    answer: GroundTruthAnswerInput,
    wm,
    db_pool,
) -> None:
    state = await wm.get_state(str(session_id))
    raw_pending = state.signal_pending_gt_tap
    if not raw_pending:
        log.info("ground_truth_answer.ignored", reason="no_pending_tap")
        return
    pending = json.loads(raw_pending)

    value = (answer.option_label or answer.free_text or "").strip()

    if answer.skipped:
        if pending.get("kind") == "ground_truth" and pending.get("field"):
            await wm.add_gt_declined_field(str(session_id), pending["field"])
        log.info("ground_truth_answer.skipped", field=pending.get("field"))
    elif pending.get("kind") == "segment_anchor":
        if value:
            await wm.set_segment_anchor(
                str(session_id),
                question_text=pending.get("question_text", ""),
                answer=value,
            )
            log.info("ground_truth_answer.anchor_recorded")
    elif pending.get("kind") == "ground_truth" and pending.get("field") and value:
        async with db_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await upsert_ground_truth_field(
                        cur,
                        person_id,
                        field=pending["field"],
                        value=value,
                        provenance="tap",
                        confidence="high",
                    )
        log.info("ground_truth_answer.recorded", field=pending["field"])

    await wm.clear_pending_gt_tap(str(session_id))
