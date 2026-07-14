"""CRM preview: run the REAL assembler over a legacy with draft config.

The content person's judgment surface (spec 2026-07-14 §2.6): tune fields ->
preview -> read the Book (every beat + art direction) and optionally see ONE
page composited through the real compositor with the chosen visual kit. No
video render, no S3 — one big-LLM call (+ one Gemini image when the sample
page is requested).
"""

from __future__ import annotations

import io

from PIL import Image

from flashback.tribute.composer import ComposedDirectives, compose_directives
from flashback.tribute.config_schema import CampaignConfig, ProfileConfig
from flashback.tribute.repository import fetch_scene_moments_async
from flashback.tribute.theme import STORYBOOK_MAX_PAGES
from flashback.tribute_video import compose as page_compose
from flashback.tribute_video import style as tv_style
from flashback.tribute_video.assembler import assemble_storybook_video
from flashback.tribute_video.book import Book


def profile_from_payload(payload: dict, *, row_id: str = "draft") -> ProfileConfig:
    """A validated CRM payload as an in-memory (unsaved) ProfileConfig."""
    return ProfileConfig(
        id=row_id,
        group_slug=payload.get("group_slug", "draft"),
        display_name=payload.get("display_name", ""),
        synonyms=tuple(payload.get("synonyms") or ()),
        voice=payload.get("voice") or {},
        opener=payload.get("opener") or {},
        art=payload.get("art") or {},
        fallback_opener=payload.get("fallback_opener", ""),
        fallback_closing=payload.get("fallback_closing", ""),
        archetype_bank=payload.get("archetype_bank"),
        message_invitation_copy=payload.get("message_invitation_copy"),
        deage_cover=bool(payload.get("deage_cover", False)),
        video_target_seconds=payload.get("video_target_seconds"),
        visual_theme_id=payload.get("visual_theme_id"),
        state="draft",
        version=0,
    )


def campaign_from_payload(payload: dict, *, row_id: str = "draft") -> CampaignConfig:
    return CampaignConfig(
        id=row_id,
        slug=payload.get("slug", "draft"),
        display_name=payload.get("display_name", ""),
        message_card_copy=payload.get("message_card_copy"),
        archetype_extra_context=payload.get("archetype_extra_context", ""),
        video_target_seconds=payload.get("video_target_seconds"),
        featured=bool(payload.get("featured", False)),
        active_start=None,
        active_end=None,
        archetype_bank_override=payload.get("archetype_bank_override"),
        deage_cover_override=payload.get("deage_cover_override"),
        visual_theme_id=payload.get("visual_theme_id"),
        closing_card_copy=payload.get("closing_card_copy"),
        state="draft",
        version=0,
    )


def _beat_dict(beat) -> dict:
    return {
        "line": beat.line,
        "art_direction": beat.art_direction,
        "moment_id": getattr(beat, "moment_id", ""),
    }


def book_to_dict(book: Book) -> dict:
    return {
        "cover_title": book.cover_title,
        "opener": _beat_dict(book.opener),
        "beats": [_beat_dict(b) for b in book.beats],
        "closing": _beat_dict(book.closing),
        "message": book.message,
    }


async def build_preview(
    settings,
    cur,
    *,
    person_id: str,
    profile: ProfileConfig,
    campaign: CampaignConfig,
) -> tuple[dict, Book, ComposedDirectives]:
    """Assemble a Book from the person's real qualifying moments with the
    given config. Returns (payload, book, directives) — the route composites
    the optional sample page from the book."""
    await cur.execute(
        "SELECT name, relationship FROM persons WHERE id = %s", (person_id,)
    )
    row = await cur.fetchone()
    subject_name = (row[0] if row else "") or "Someone"
    relationship = row[1] if row else None
    candidates = await fetch_scene_moments_async(
        cur, person_id=person_id, limit=STORYBOOK_MAX_PAGES
    )
    await cur.execute(
        "SELECT message_text FROM tributes "
        "WHERE person_id = %s AND message_text IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (person_id,),
    )
    msg_row = await cur.fetchone()

    directives = compose_directives(profile, campaign)
    book = await assemble_storybook_video(
        settings=settings,
        subject_name=subject_name,
        relationship=relationship,
        gt_context="",
        candidates=candidates,
        message_text=(msg_row[0] if msg_row else "") or "",
        n_pages=STORYBOOK_MAX_PAGES,
        voice_block=directives.voice_block,
        opener_style=directives.opener_style,
        art_mood=directives.art_mood,
        fallback_opener=directives.fallback_opener,
        fallback_closing=directives.fallback_closing,
        feature="tribute_preview",
    )
    payload = {
        "book": book_to_dict(book),
        "resolved": {
            "profile_id": profile.id,
            "group_slug": profile.group_slug,
            "campaign_id": campaign.id,
            "campaign_slug": campaign.slug,
            "visual_theme_id": directives.visual_theme_id,
            "candidate_count": len(candidates),
        },
    }
    return payload, book, directives


def render_sample_page(
    artist,
    *,
    book: Book,
    kit: tv_style.StyleKit,
    role: str = "opener",
    beat_index: int = 0,
) -> bytes:
    """One page, composited exactly as the renderer would. JPEG bytes."""
    if role == "closing":
        beat = book.closing
    elif role == "beat" and book.beats:
        beat = book.beats[min(max(beat_index, 0), len(book.beats) - 1)]
    else:
        role, beat = "opener", book.opener

    illo: Image.Image | None = None
    if beat.art_direction.strip():
        illo = artist.illustrate(beat.art_direction, "", "cream")
    layout = tv_style.layout_for(role, beat_index)
    page = page_compose.compose_page(
        eyebrow=getattr(beat, "eyebrow", ""),
        line=beat.line,
        illo=illo,
        blend="cream",
        layout=layout,
        kit=kit,
    )
    buf = io.BytesIO()
    page.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
