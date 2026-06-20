"""Mint, regenerate, and edit standalone storybooks ON DEMAND.

A storybook is a keepsake book of memories compiled from a person's qualifying
moments. Unlike a tribute it has NO contributor message and NO cover page --
the assembler's closing line is the final card. It reuses the tribute Sonnet
assembler + storybook context builder + PDF renderer verbatim, minus the cover
(``include_cover=False``), and carries 1-3 emotional tags from the fixed
registry (``flashback.storybook.tags``) that tone the prose and drive Node's
template choice.

Three entry points, each self-contained (DB read -> LLM -> DB write -> enqueue):
  * ``generate_storybook``   -- new book from the (optionally scoped) pool.
  * ``regenerate_storybook`` -- re-render the existing script with a new preset
    / tags (text kept).
  * ``edit_storybook``       -- re-run the assembler over the same moments with
    cumulative edit instructions to reshape text + scenes, then re-render.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

import structlog
from psycopg_pool import AsyncConnectionPool

from flashback.artifacts import people_scene_fragment
from flashback.artifacts.presets import resolve_preset
from flashback.config import HttpConfig
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.queues.artifact_generation import ArtifactGenerationQueueProducer
from flashback.storybook.repository import (
    STORYBOOK_MIN_MOMENTS,
    fetch_moments_by_ids_async,
    fetch_person_for_storybook_async,
    fetch_scope_scene_moments_async,
    fetch_storybook_for_regen_async,
    insert_storybook_async,
    update_storybook_after_edit_async,
    update_storybook_after_regen_async,
)
from flashback.storybook.tags import (
    labels_for,
    normalize_tags,
    render_tag_catalog,
)
from flashback.tribute.artifact_context import build_storybook_context
from flashback.tribute.assembly import Scene, TributeScript, assemble_tribute_script
from flashback.tribute.theme import STORYBOOK_MAX_PAGES

log = structlog.get_logger("flashback.storybook.generation")


class StorybookTooThin(Exception):
    """Raised when the (scoped) qualifying pool is below the floor to mint one."""

    def __init__(self, available: int) -> None:
        self.available = available
        super().__init__(
            f"need at least {STORYBOOK_MIN_MOMENTS} qualifying moments "
            f"(have {available})"
        )


class StorybookNotFound(Exception):
    """Raised when a regenerate/edit targets a missing/unowned storybook."""


@dataclass(frozen=True)
class StorybookGenerationResult:
    storybook_id: str
    job_id: str
    tags: list[str]
    moments_count: int
    scene_count: int
    enqueued: bool


# ---------------------------------------------------------------------------
# Script <-> JSON round-trip (so regenerate/edit can rebuild the context)
# ---------------------------------------------------------------------------


def _script_to_json(script: TributeScript) -> dict[str, Any]:
    """Serialize a TributeScript to the ``storybooks.script`` JSON shape.

    Includes ``art_direction`` so regenerate preserves each beat's authored
    visual brief instead of falling back to the generic moment prompt.
    """
    return {
        "scenes": [
            {
                "moment_id": s.moment_id,
                "caption": s.caption,
                "accent": s.accent,
                "pull_quote": s.pull_quote,
                "layout": s.layout,
                "art_direction": s.art_direction,
            }
            for s in script.scenes
        ],
        "opening_caption": script.opening_caption,
        "closing_caption": script.closing_caption,
        "message_text": script.message_text,
        "cover_title": script.cover_title,
        "cover_prompt": script.cover_prompt,
        "tags": list(script.tags),
    }


def _script_from_json(data: dict[str, Any]) -> TributeScript:
    """Rebuild a TributeScript from its stored ``storybooks.script`` JSON."""
    raw_scenes = data.get("scenes") or []
    scenes = [
        Scene(
            moment_id=s.get("moment_id", ""),
            caption=(s.get("caption") or "").strip(),
            accent=(s.get("accent") or "").strip(),
            pull_quote=(s.get("pull_quote") or "").strip(),
            layout=(s.get("layout") or "").strip(),
            art_direction=(s.get("art_direction") or "").strip(),
        )
        for s in raw_scenes
        if isinstance(s, dict) and s.get("moment_id")
    ]
    return TributeScript(
        scenes=scenes,
        opening_caption=(data.get("opening_caption") or "").strip(),
        closing_caption=(data.get("closing_caption") or "").strip(),
        message_text=(data.get("message_text") or "").strip(),
        cover_title=(data.get("cover_title") or "").strip(),
        cover_prompt=(data.get("cover_prompt") or "").strip(),
        tags=tuple(data.get("tags") or ()),
    )


def _finalize_book_script(script: TributeScript, person_name: str) -> TributeScript:
    """Promote the closing line to the final card (storybook has no message).

    The standalone book ends on the assembler's closing line, so it is moved
    into ``message_text`` (the final-page text) and ``closing_caption`` cleared.
    """
    closing_line = (script.closing_caption or "").strip() or f"The story of {person_name}"
    return replace(script, message_text=closing_line, closing_caption="")


# ---------------------------------------------------------------------------
# Shared context build
# ---------------------------------------------------------------------------


async def _person_render_context(
    db_pool: AsyncConnectionPool, person_id: str, person: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Fetch the ground-truth scene block + people-gender fragment."""
    ground_truth = await fetch_ground_truth(db_pool, person_id)
    gt_scene = render_ground_truth_block(ground_truth, "scene_subject") or None
    people_ctx = (
        people_scene_fragment(
            subject_gender=person.get("gender"),
            contributor_gender=person.get("contributor_gender"),
        )
        or None
    )
    return gt_scene, people_ctx


def _build_context(
    *,
    book_script: TributeScript,
    candidates: list[dict[str, Any]],
    preset: str,
    person_name: str,
    gt_scene: str | None,
    people_ctx: str | None,
) -> dict[str, Any]:
    moments_by_id = {c["id"]: c for c in candidates}
    return build_storybook_context(
        script=book_script,
        moments_by_id=moments_by_id,
        preset=preset,
        max_pages=STORYBOOK_MAX_PAGES,
        ground_truth_context=gt_scene,
        people_context=people_ctx,
        cover_subtitle=person_name,
        include_cover=False,
    )


def _title_for(script: TributeScript, person_name: str) -> str:
    return (script.cover_title or "").strip() or f"{person_name}'s Story"


async def _push_job(
    *,
    artifact_queue: ArtifactGenerationQueueProducer | None,
    storybook_id: str,
    person_id: str,
    source: str,
    composed_at: str,
) -> tuple[str, bool]:
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
                source=source,
                composed_at=composed_at,
            )
            enqueued = msg_id is not None
        except Exception:  # noqa: BLE001 -- enqueue is best-effort
            log.warning(
                "storybook.enqueue_failed",
                storybook_id=storybook_id,
                source=source,
                exc_info=True,
            )
    return job_id, enqueued


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def generate_storybook(
    *,
    db_pool: AsyncConnectionPool,
    settings: HttpConfig | None,
    artifact_queue: ArtifactGenerationQueueProducer | None,
    person_id: str,
    theme_id: str | None = None,
    life_period: str | None = None,
    preset: str | None = None,
) -> StorybookGenerationResult:
    """Mint a new on-demand storybook from the (optionally scoped) pool."""
    preset_slug = resolve_preset(preset)

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            person = await fetch_person_for_storybook_async(cur, person_id=person_id)
            candidates = await fetch_scope_scene_moments_async(
                cur,
                person_id=person_id,
                theme_id=theme_id,
                life_period=life_period,
            )
    if person is None:
        raise StorybookNotFound("person not found")
    if len(candidates) < STORYBOOK_MIN_MOMENTS:
        raise StorybookTooThin(len(candidates))

    gt_scene, people_ctx = await _person_render_context(db_pool, person_id, person)
    person_name = person["person_name"] or ""

    script = await assemble_tribute_script(
        settings=settings,
        candidates=candidates,
        message_text="",
        person_name=person_name,
        person_relationship=person["person_relationship"],
        max_scenes=STORYBOOK_MAX_PAGES - 1,
        tag_catalog=render_tag_catalog(),
    )
    tags = normalize_tags(script.tags)
    book_script = replace(_finalize_book_script(script, person_name), tags=tuple(tags))
    context = _build_context(
        book_script=book_script,
        candidates=candidates,
        preset=preset_slug,
        person_name=person_name,
        gt_scene=gt_scene,
        people_ctx=people_ctx,
    )
    title = _title_for(script, person_name)
    script_json = _script_to_json(book_script)
    scene_ids = [s.moment_id for s in book_script.scenes]

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                storybook_id = await insert_storybook_async(
                    cur,
                    person_id=person_id,
                    title=title,
                    script=script_json,
                    scene_moment_ids=scene_ids,
                    moments_count=len(candidates),
                    context=context,
                    tags=tags,
                )

    job_id, enqueued = await _push_job(
        artifact_queue=artifact_queue,
        storybook_id=storybook_id,
        person_id=person_id,
        source="manual",
        composed_at=context["composed_at"],
    )
    log.info(
        "storybook.generated",
        person_id=person_id,
        storybook_id=storybook_id,
        scene_count=len(scene_ids),
        moments_count=len(candidates),
        tags=tags,
        enqueued=enqueued,
    )
    return StorybookGenerationResult(
        storybook_id=storybook_id,
        job_id=job_id,
        tags=tags,
        moments_count=len(candidates),
        scene_count=len(scene_ids),
        enqueued=enqueued,
    )


async def regenerate_storybook(
    *,
    db_pool: AsyncConnectionPool,
    settings: HttpConfig | None,
    artifact_queue: ArtifactGenerationQueueProducer | None,
    storybook_id: str,
    person_id: str,
    preset: str | None = None,
    tags: list[str] | None = None,
) -> StorybookGenerationResult:
    """Re-render an existing storybook (script kept) with a new preset / tags.

    The captions, ordering, and art direction are kept verbatim; only the page
    image prompts are re-composed (with the new preset) and the tags optionally
    overridden for Node's template selection. No LLM call.
    """
    preset_slug = resolve_preset(preset)

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            row = await fetch_storybook_for_regen_async(
                cur, storybook_id=storybook_id, person_id=person_id
            )
            if row is None:
                raise StorybookNotFound("storybook not found for this person")
            person = await fetch_person_for_storybook_async(cur, person_id=person_id)
            candidates = await fetch_moments_by_ids_async(
                cur, person_id=person_id, moment_ids=row["scene_moment_ids"]
            )
    if person is None:
        raise StorybookNotFound("person not found")

    final_tags = normalize_tags(tags) if tags is not None else normalize_tags(row["tags"])
    gt_scene, people_ctx = await _person_render_context(db_pool, person_id, person)
    person_name = person["person_name"] or ""

    book_script = replace(_script_from_json(row["script"]), tags=tuple(final_tags))
    context = _build_context(
        book_script=book_script,
        candidates=candidates,
        preset=preset_slug,
        person_name=person_name,
        gt_scene=gt_scene,
        people_ctx=people_ctx,
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await update_storybook_after_regen_async(
                    cur,
                    storybook_id=storybook_id,
                    context=context,
                    tags=final_tags,
                )

    job_id, enqueued = await _push_job(
        artifact_queue=artifact_queue,
        storybook_id=storybook_id,
        person_id=person_id,
        source="regenerate",
        composed_at=context["composed_at"],
    )
    log.info(
        "storybook.regenerated",
        person_id=person_id,
        storybook_id=storybook_id,
        tags=final_tags,
        enqueued=enqueued,
    )
    return StorybookGenerationResult(
        storybook_id=storybook_id,
        job_id=job_id,
        tags=final_tags,
        moments_count=row["moments_count"] or len(candidates),
        scene_count=len(book_script.scenes),
        enqueued=enqueued,
    )


async def edit_storybook(
    *,
    db_pool: AsyncConnectionPool,
    settings: HttpConfig | None,
    artifact_queue: ArtifactGenerationQueueProducer | None,
    storybook_id: str,
    person_id: str,
    instructions: str,
    prior_instructions: list[str] | None = None,
    preset: str | None = None,
    tags: list[str] | None = None,
) -> StorybookGenerationResult:
    """Reshape an existing storybook's text + scenes per cumulative instructions.

    Re-runs the assembler over the SAME moment set (the stored
    ``scene_moment_ids``) with the cumulative edit notes, so it can drop /
    reorder / re-tone scenes -- but not pull in new moments (that is a fresh
    ``generate``). When ``tags`` is supplied the prose is re-toned to that
    register; otherwise the assembler re-picks tags from the registry.
    """
    preset_slug = resolve_preset(preset)
    prior = list(prior_instructions or [])

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            row = await fetch_storybook_for_regen_async(
                cur, storybook_id=storybook_id, person_id=person_id
            )
            if row is None:
                raise StorybookNotFound("storybook not found for this person")
            person = await fetch_person_for_storybook_async(cur, person_id=person_id)
            candidates = await fetch_moments_by_ids_async(
                cur, person_id=person_id, moment_ids=row["scene_moment_ids"]
            )
    if person is None:
        raise StorybookNotFound("person not found")
    if not candidates:
        raise StorybookTooThin(0)

    gt_scene, people_ctx = await _person_render_context(db_pool, person_id, person)
    person_name = person["person_name"] or ""

    edit_directive = "\n".join(s for s in [*prior, instructions] if s and s.strip())
    forced = normalize_tags(tags) if tags is not None else []
    style_directive = None
    tag_catalog: str | None = render_tag_catalog()
    if forced:
        # Caller pinned the register: force the tone, skip auto-pick.
        style_directive = (
            "Write the whole book in a "
            f"{', '.join(labels_for(forced)).lower()} register."
        )
        tag_catalog = None

    script = await assemble_tribute_script(
        settings=settings,
        candidates=candidates,
        message_text="",
        person_name=person_name,
        person_relationship=person["person_relationship"],
        max_scenes=STORYBOOK_MAX_PAGES - 1,
        tag_catalog=tag_catalog,
        style_directive=style_directive,
        edit_directive=edit_directive,
    )
    final_tags = forced or normalize_tags(script.tags) or normalize_tags(row["tags"])
    book_script = replace(
        _finalize_book_script(script, person_name), tags=tuple(final_tags)
    )
    context = _build_context(
        book_script=book_script,
        candidates=candidates,
        preset=preset_slug,
        person_name=person_name,
        gt_scene=gt_scene,
        people_ctx=people_ctx,
    )
    title = _title_for(script, person_name)
    script_json = _script_to_json(book_script)
    scene_ids = [s.moment_id for s in book_script.scenes]

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await update_storybook_after_edit_async(
                    cur,
                    storybook_id=storybook_id,
                    title=title,
                    script=script_json,
                    scene_moment_ids=scene_ids,
                    context=context,
                    tags=final_tags,
                )

    job_id, enqueued = await _push_job(
        artifact_queue=artifact_queue,
        storybook_id=storybook_id,
        person_id=person_id,
        source="edit",
        composed_at=context["composed_at"],
    )
    log.info(
        "storybook.edited",
        person_id=person_id,
        storybook_id=storybook_id,
        scene_count=len(scene_ids),
        tags=final_tags,
        enqueued=enqueued,
    )
    return StorybookGenerationResult(
        storybook_id=storybook_id,
        job_id=job_id,
        tags=final_tags,
        moments_count=row["moments_count"] or len(candidates),
        scene_count=len(scene_ids),
        enqueued=enqueued,
    )
