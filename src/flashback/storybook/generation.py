"""Mint, regenerate, and edit collection storybooks (Python render pipeline).

A storybook is one of six fixed collections rendered as a 7-page illustrated
book (spec 2026-06-29, validated by the storybook_comic_prototype spike). The
route work here is deliberately light: validate, fetch the qualifying pool,
write the render context onto the row, enqueue ``storybook_render``. ALL
heavy LLM work (curation + script assembly + Gemini art) happens in the
worker — the request returns immediately (the tribute pattern).

Three entry points:
  * ``generate_storybook``   -- new book for a chosen collection.
  * ``regenerate_storybook`` -- redraw the art, keep the stored script.
  * ``edit_storybook``       -- re-assemble with cumulative edit requests.

Node mints the presigned URLs (pdf + cover + PAGE_COUNT pages, plus the
optional anchor-photo GET per the latest-profile-picture-context rule) and
LISTENs ``storybook_render_complete`` to write the URL columns. The old
``artifact_generation`` path for storybooks is retired.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.queues.storybook_render import StorybookRenderQueueProducer
from flashback.storybook.collections import COLLECTIONS, PAGE_COUNT
from flashback.storybook.context import CONTEXT_KEY, build_context_dict
from flashback.storybook.repository import (
    STORYBOOK_MAX_SELECT,
    STORYBOOK_MIN_MOMENTS,
    effective_min_select,
    fetch_moments_by_ids_async,
    fetch_person_for_storybook_async,
    fetch_scope_scene_moments_async,
    fetch_storybook_for_regen_async,
    insert_storybook_async,
    update_storybook_for_rerender_async,
)

log = structlog.get_logger("flashback.storybook.generation")


class UnknownCollection(Exception):
    """Raised when the requested collection slug is not in the registry."""

    def __init__(self, slug: str) -> None:
        super().__init__(
            f"unknown collection {slug!r}; valid: {sorted(COLLECTIONS)}"
        )


class BadPageUrls(Exception):
    """Raised when the presigned page URL count does not match PAGE_COUNT."""

    def __init__(self, got: int) -> None:
        super().__init__(
            f"page_put_urls must carry exactly {PAGE_COUNT} URLs (got {got})"
        )


class StorybookTooThin(Exception):
    """Raised when the qualifying pool is below the floor to mint a book."""

    def __init__(self, available: int, person_name: str | None = None) -> None:
        self.available = available
        who = f" of {person_name}" if person_name else ""
        super().__init__(
            f"Not enough stories yet -- keep sharing memories{who} "
            f"(need at least {STORYBOOK_MIN_MOMENTS} qualifying moments, "
            f"have {available})"
        )


class StorybookNotFound(Exception):
    """Raised when a generate/regenerate/edit targets a missing person or
    a missing/unowned storybook."""


class StorybookIdConflict(Exception):
    """Raised when the caller-supplied storybook_id already exists."""

    def __init__(self, storybook_id: str) -> None:
        super().__init__(f"storybook_id {storybook_id} already exists")


class StorybookBadMomentIds(Exception):
    """Raised when a confirmed selection contains ids outside the pool."""

    def __init__(self, bad_ids: list[str]) -> None:
        self.bad_ids = list(bad_ids)
        shown = ", ".join(self.bad_ids[:5])
        super().__init__(f"unknown or non-qualifying moment ids: {shown}")


class StorybookSelectionOutOfBounds(Exception):
    """Raised when a confirmed selection misses the min/max bounds."""

    def __init__(self, got: int, min_select: int, max_select: int) -> None:
        super().__init__(
            f"pick between {min_select} and {max_select} moments "
            f"(got {got})"
        )


def _resolve_selection(
    pool: list[dict[str, Any]], moment_ids: list[str]
) -> list[dict[str, Any]]:
    """Dedupe + resolve the confirmed ids against the qualifying pool.

    Pool membership is the validation (person-scoping falls out of it);
    unknown ids raise rather than silently dropping -- an explicit user
    choice must never be quietly ignored.
    """
    by_id = {str(m["id"]): m for m in pool}
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    bad: list[str] = []
    for mid in (str(m) for m in moment_ids):
        if mid in seen:
            continue
        seen.add(mid)
        if mid in by_id:
            ordered.append(by_id[mid])
        else:
            bad.append(mid)
    if bad:
        raise StorybookBadMomentIds(bad)
    lo = effective_min_select(len(pool))
    if not (lo <= len(ordered) <= STORYBOOK_MAX_SELECT):
        raise StorybookSelectionOutOfBounds(
            len(ordered), lo, STORYBOOK_MAX_SELECT
        )
    return ordered


@dataclass(frozen=True)
class StorybookGenerationResult:
    storybook_id: str
    job_id: str
    collection: str
    moments_count: int
    enqueued: bool


def _validate(collection: str, page_put_urls: list[str]) -> None:
    if collection not in COLLECTIONS:
        raise UnknownCollection(collection)
    if len(page_put_urls) != PAGE_COUNT:
        raise BadPageUrls(len(page_put_urls))


def _moments_payload(moments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The compact per-moment payload the context carries. ``life_period`` +
    ``time_anchor`` ride along so the assembler can place events on the
    subject's timeline and state everyone's age (the age-drift fix)."""
    return [
        {
            "id": str(m.get("id") or ""),
            "title": m.get("title") or "",
            "narrative": m.get("narrative") or "",
            "life_period": m.get("life_period") or "",
            "time_anchor": m.get("time_anchor") or None,
        }
        for m in moments
    ]


async def _fetch_inputs(
    db_pool: AsyncConnectionPool, person_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Person descriptors + qualifying pool + ground-truth block, or raise."""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            person = await fetch_person_for_storybook_async(
                cur, person_id=person_id
            )
            if person is None:
                raise StorybookNotFound(f"person {person_id} not found")
            moments = await fetch_scope_scene_moments_async(
                cur, person_id=person_id
            )
    if len(moments) < STORYBOOK_MIN_MOMENTS:
        raise StorybookTooThin(
            len(moments), person_name=person.get("person_name")
        )
    ground_truth = await fetch_ground_truth(db_pool, person_id)
    gt_context = render_ground_truth_block(ground_truth, "scene_subject") or ""
    return person, moments, gt_context


def _context(
    *,
    collection: str,
    person: dict[str, Any],
    moments: list[dict[str, Any]],
    gt_context: str,
    pdf_put_url: str,
    cover_put_url: str,
    page_put_urls: list[str],
    anchor_photo_get_url: str | None,
    edit_instructions: list[str] | None = None,
    reuse_script: bool = False,
    user_curated: bool = False,
) -> tuple[dict[str, Any], str]:
    composed_at = datetime.now(timezone.utc).isoformat()
    ctx = build_context_dict(
        collection=collection,
        subject_name=person.get("person_name") or "",
        relationship=person.get("person_relationship"),
        gt_context=gt_context,
        gender=person.get("gender"),
        moments=_moments_payload(moments),
        pdf_put_url=pdf_put_url,
        cover_put_url=cover_put_url,
        page_put_urls=list(page_put_urls),
        anchor_photo_get_url=anchor_photo_get_url or "",
        edit_instructions=edit_instructions,
        reuse_script=reuse_script,
        user_curated=user_curated,
        composed_at=composed_at,
    )
    return ctx, composed_at


async def _enqueue(
    queue: StorybookRenderQueueProducer | None,
    *,
    storybook_id: str,
    person_id: str,
    composed_at: str,
) -> tuple[str, bool]:
    job_id = str(uuid4())
    enqueued = False
    if queue is not None:
        try:
            msg_id = await queue.push(
                job_id=job_id,
                storybook_id=storybook_id,
                person_id=person_id,
                composed_at=composed_at,
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning(
                "storybook.enqueue_failed",
                storybook_id=storybook_id,
                exc_info=True,
            )
    return job_id, enqueued


async def generate_storybook(
    *,
    db_pool: AsyncConnectionPool,
    queue: StorybookRenderQueueProducer | None,
    person_id: str,
    collection: str,
    pdf_put_url: str,
    cover_put_url: str,
    page_put_urls: list[str],
    anchor_photo_get_url: str | None = None,
    storybook_id: str | None = None,
    moment_ids: list[str] | None = None,
) -> StorybookGenerationResult:
    """Mint a new collection storybook: context on the row, then enqueue.

    ``storybook_id`` is caller-supplied (Node generates it so the presigned
    S3 keys it minted embed a known id; its completion listener re-derives
    keys from the id with no persistence).

    ``moment_ids`` is the optional user-confirmed selection from the
    preview flow (spec 2026-07-05). Absent = auto-curate as before.
    """
    _validate(collection, page_put_urls)
    person, moments, gt_context = await _fetch_inputs(db_pool, person_id)
    user_curated = moment_ids is not None
    if user_curated:
        moments = _resolve_selection(moments, moment_ids)
    ctx, composed_at = _context(
        collection=collection,
        person=person,
        moments=moments,
        gt_context=gt_context,
        pdf_put_url=pdf_put_url,
        cover_put_url=cover_put_url,
        page_put_urls=page_put_urls,
        anchor_photo_get_url=anchor_photo_get_url,
        user_curated=user_curated,
    )
    # Context to Postgres FIRST; the SQS message is a trigger only (§3).
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                storybook_id = await insert_storybook_async(
                    cur,
                    person_id=person_id,
                    title=None,  # worker writes it from the assembled script
                    script={},
                    scene_moment_ids=(
                        [str(m["id"]) for m in moments]
                        if user_curated
                        else []
                    ),
                    moments_count=len(moments),
                    context={CONTEXT_KEY: ctx},
                    tags=[],
                    collection=collection,
                    storybook_id=storybook_id,
                )
    except UniqueViolation as exc:
        raise StorybookIdConflict(str(storybook_id)) from exc
    job_id, enqueued = await _enqueue(
        queue,
        storybook_id=storybook_id,
        person_id=person_id,
        composed_at=composed_at,
    )
    log.info(
        "storybook.generate_enqueued",
        storybook_id=storybook_id,
        collection=collection,
        moments=len(moments),
        enqueued=enqueued,
    )
    return StorybookGenerationResult(
        storybook_id=storybook_id,
        job_id=job_id,
        collection=collection,
        moments_count=len(moments),
        enqueued=enqueued,
    )


async def _rerender(
    *,
    db_pool: AsyncConnectionPool,
    queue: StorybookRenderQueueProducer | None,
    storybook_id: str,
    person_id: str,
    pdf_put_url: str,
    cover_put_url: str,
    page_put_urls: list[str],
    anchor_photo_get_url: str | None,
    edit_instructions: list[str] | None,
    reuse_script: bool,
    source: str,
) -> StorybookGenerationResult:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            row = await fetch_storybook_for_regen_async(
                cur, storybook_id=storybook_id, person_id=person_id
            )
    if row is None:
        raise StorybookNotFound(
            f"storybook {storybook_id} not found for person {person_id}"
        )
    collection = row.get("collection") or ""
    _validate(collection, page_put_urls)
    person, moments, gt_context = await _fetch_inputs(db_pool, person_id)
    # A user-curated book keeps its confirmed slice across regenerate /
    # edit; superseded ids fall out of fetch_moments_by_ids. If that
    # guts the slice below the floor, fall back to the full pool (auto)
    # rather than stranding the edit -- there is no post-render re-pick
    # surface (spec 2026-07-05 §5).
    stored_ctx = ((row.get("context") or {}).get(CONTEXT_KEY)) or {}
    user_curated = bool(stored_ctx.get("user_curated"))
    if user_curated:
        kept_ids = [
            str(m["id"])
            for m in (stored_ctx.get("moments") or [])
            if m.get("id")
        ]
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                selected = await fetch_moments_by_ids_async(
                    cur, person_id=person_id, moment_ids=kept_ids
                )
        if len(selected) >= STORYBOOK_MIN_MOMENTS:
            moments = selected
        else:
            log.warning(
                "storybook.selection_below_floor_fallback",
                storybook_id=storybook_id,
                kept=len(selected),
            )
            user_curated = False
    ctx, composed_at = _context(
        collection=collection,
        person=person,
        moments=moments,
        gt_context=gt_context,
        pdf_put_url=pdf_put_url,
        cover_put_url=cover_put_url,
        page_put_urls=page_put_urls,
        anchor_photo_get_url=anchor_photo_get_url,
        edit_instructions=edit_instructions,
        reuse_script=reuse_script,
        user_curated=user_curated,
    )
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            updated = await update_storybook_for_rerender_async(
                cur,
                storybook_id=storybook_id,
                person_id=person_id,
                context={CONTEXT_KEY: ctx},
            )
    if not updated:
        raise StorybookNotFound(
            f"storybook {storybook_id} not found for person {person_id}"
        )
    job_id, enqueued = await _enqueue(
        queue,
        storybook_id=storybook_id,
        person_id=person_id,
        composed_at=composed_at,
    )
    log.info(
        "storybook.rerender_enqueued",
        storybook_id=storybook_id,
        collection=collection,
        source=source,
        reuse_script=reuse_script,
        enqueued=enqueued,
    )
    return StorybookGenerationResult(
        storybook_id=storybook_id,
        job_id=job_id,
        collection=collection,
        moments_count=len(moments),
        enqueued=enqueued,
    )


async def regenerate_storybook(
    *,
    db_pool: AsyncConnectionPool,
    queue: StorybookRenderQueueProducer | None,
    storybook_id: str,
    person_id: str,
    pdf_put_url: str,
    cover_put_url: str,
    page_put_urls: list[str],
    anchor_photo_get_url: str | None = None,
) -> StorybookGenerationResult:
    """Redraw the art with the stored script (text kept)."""
    return await _rerender(
        db_pool=db_pool,
        queue=queue,
        storybook_id=storybook_id,
        person_id=person_id,
        pdf_put_url=pdf_put_url,
        cover_put_url=cover_put_url,
        page_put_urls=page_put_urls,
        anchor_photo_get_url=anchor_photo_get_url,
        edit_instructions=None,
        reuse_script=True,
        source="regenerate",
    )


async def edit_storybook(
    *,
    db_pool: AsyncConnectionPool,
    queue: StorybookRenderQueueProducer | None,
    storybook_id: str,
    person_id: str,
    instructions: str,
    prior_instructions: list[str],
    pdf_put_url: str,
    cover_put_url: str,
    page_put_urls: list[str],
    anchor_photo_get_url: str | None = None,
) -> StorybookGenerationResult:
    """Re-assemble the script honouring every accepted edit, then re-render.

    Node keeps the cumulative edit history (Dynamo per-record) and sends the
    full ``prior_instructions`` list on every call, mirroring the artifact
    edit surface.
    """
    edits = [*prior_instructions, instructions] if instructions else list(
        prior_instructions
    )
    return await _rerender(
        db_pool=db_pool,
        queue=queue,
        storybook_id=storybook_id,
        person_id=person_id,
        pdf_put_url=pdf_put_url,
        cover_put_url=cover_put_url,
        page_put_urls=page_put_urls,
        anchor_photo_get_url=anchor_photo_get_url,
        edit_instructions=edits,
        reuse_script=False,
        source="edit",
    )
