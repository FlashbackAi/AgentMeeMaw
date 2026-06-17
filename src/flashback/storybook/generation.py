"""Mint a standalone storybook edition at Session Wrap.

Count-gated (CLAUDE.md cold-start cadence family): only fires when enough new
qualifying moments have accumulated since the last edition. When it fires it
reuses the tribute assembler + storybook context builder + PDF renderer, but
with NO contributor message -- the final card is the assembler's closing line.

Pure-ish split:
  * ``assemble_storybook`` -- LLM assembly + context compose, no DB.
  * ``maybe_generate_storybook`` -- gate, persist, push; safe to call on every
    wrap (it no-ops when the gate is not satisfied).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

import structlog
from psycopg_pool import AsyncConnectionPool

from flashback.artifacts.presets import resolve_preset
from flashback.config import HttpConfig
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.queues.artifact_generation import ArtifactGenerationQueueProducer
from flashback.storybook.repository import (
    fetch_person_for_storybook_async,
    insert_storybook_async,
    stamp_moments_at_last_storybook_run_async,
    storybook_gate_async,
)
from flashback.tribute.artifact_context import build_storybook_context
from flashback.tribute.assembly import assemble_tribute_script
from flashback.tribute.repository import fetch_scene_moments_async
from flashback.tribute.theme import STORYBOOK_MAX_PAGES

log = structlog.get_logger("flashback.storybook.generation")


@dataclass(frozen=True)
class StorybookGenerationResult:
    generated: bool
    reason: str
    storybook_id: str | None = None
    job_id: str | None = None
    enqueued: bool = False


async def assemble_storybook(
    *,
    settings: HttpConfig | None,
    candidates: list[dict],
    person_name: str,
    person_relationship: str | None,
    preset: str,
    ground_truth_context: str | None,
) -> tuple[str | None, dict, dict]:
    """Assemble script + storybook context. Returns (title, script_json, context).

    Storybook carries no contributor message: the assembler's closing line is
    promoted to the final card, and the contributor-message slot is emptied.
    """
    script = await assemble_tribute_script(
        settings=settings,
        candidates=candidates,
        message_text="",
        person_name=person_name,
        person_relationship=person_relationship,
        max_scenes=STORYBOOK_MAX_PAGES - 1,
    )

    closing_line = (script.closing_caption or "").strip() or f"The story of {person_name}"
    # Final card = the closing line (centered); no muted bottom line.
    book_script = replace(script, message_text=closing_line, closing_caption="")

    moments_by_id = {c["id"]: c for c in candidates}
    context = build_storybook_context(
        script=book_script,
        moments_by_id=moments_by_id,
        preset=preset,
        max_pages=STORYBOOK_MAX_PAGES,
        ground_truth_context=ground_truth_context,
        cover_subtitle=person_name,
    )

    title = (script.cover_title or "").strip() or f"{person_name}'s Story"
    script_json = {
        "scenes": [
            {
                "moment_id": s.moment_id,
                "caption": s.caption,
                "accent": s.accent,
                "pull_quote": s.pull_quote,
                "layout": s.layout,
            }
            for s in script.scenes
        ],
        "opening_caption": script.opening_caption,
        "closing_caption": script.closing_caption,
        "cover_title": script.cover_title,
        "cover_prompt": script.cover_prompt,
    }
    return title, script_json, context


async def maybe_generate_storybook(
    *,
    db_pool: AsyncConnectionPool,
    settings: HttpConfig | None,
    artifact_queue: ArtifactGenerationQueueProducer | None,
    person_id: str,
    preset: str | None = None,
) -> StorybookGenerationResult:
    """Gate, and on pass mint a new storybook edition + push the artifact job.

    No-ops (generated=False) when the count-gate is not satisfied, so it is
    safe to call unconditionally at the tail of every Session Wrap.
    """
    preset_slug = resolve_preset(preset)

    # 1) Gate + gather inputs (read-only).
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            gate = await storybook_gate_async(cur, person_id=person_id)
            if not gate.valid:
                return StorybookGenerationResult(
                    generated=False,
                    reason=(
                        f"gate_not_met (qualifying={gate.qualifying_count} "
                        f"delta={gate.delta})"
                    ),
                )
            person = await fetch_person_for_storybook_async(cur, person_id=person_id)
            candidates = await fetch_scene_moments_async(
                cur, person_id=person_id, limit=12
            )
    if person is None:
        return StorybookGenerationResult(generated=False, reason="person_missing")

    ground_truth = await fetch_ground_truth(db_pool, person_id)
    gt_scene = render_ground_truth_block(ground_truth, "scene") or None

    # 2) Assemble (LLM) outside the write transaction.
    title, script_json, context = await assemble_storybook(
        settings=settings,
        candidates=candidates,
        person_name=person["person_name"] or "",
        person_relationship=person["person_relationship"],
        preset=preset_slug,
        ground_truth_context=gt_scene,
    )
    scene_ids = [s["moment_id"] for s in script_json["scenes"]]

    # 3) Persist edition + stamp the watermark atomically.
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                storybook_id = await insert_storybook_async(
                    cur,
                    person_id=person_id,
                    title=title,
                    script=script_json,
                    scene_moment_ids=scene_ids,
                    moments_count=gate.qualifying_count,
                    context=context,
                )
                await stamp_moments_at_last_storybook_run_async(
                    cur, person_id=person_id, count=gate.qualifying_count
                )

    # 4) Push the trigger-only job (after the row + context are committed).
    job_id = str(uuid4())
    enqueued = False
    if artifact_queue is not None:
        try:
            msg_id = await artifact_queue.push(
                job_id=job_id,
                record_type="storybook",
                record_id=storybook_id,
                person_id=person_id,
                artifact_kind="storybook",
                source="auto",
                composed_at=context["composed_at"],
            )
            enqueued = msg_id is not None
        except Exception:  # noqa: BLE001
            log.warning("storybook.enqueue_failed", storybook_id=storybook_id, exc_info=True)

    log.info(
        "storybook.generated",
        person_id=person_id,
        storybook_id=storybook_id,
        scene_count=len(scene_ids),
        moments_count=gate.qualifying_count,
        enqueued=enqueued,
    )
    return StorybookGenerationResult(
        generated=True,
        reason="ok",
        storybook_id=storybook_id,
        job_id=job_id,
        enqueued=enqueued,
    )
