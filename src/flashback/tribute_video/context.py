"""The render context the worker reads from Postgres.

Written by ``POST /tributes/{id}/generate`` into
``tributes.latest_generation_context['tribute_video']`` (Postgres authoritative;
the SQS message is a trigger only). Carries the assembly INPUTS (subject
descriptors, candidate moments, message, leads) plus the Node-minted presigned
URLs and render knobs. The Book is assembled by the worker (a big-LLM call) at
render time -- NOT in the HTTP request -- so ``/generate`` returns immediately
instead of blocking ~30s and tripping Node's request timeout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTEXT_KEY = "tribute_video"


def _anchor_year(anchor: Any) -> int | None:
    """A 4-digit year out of a moment's ``time_anchor``, when it has one."""
    if not isinstance(anchor, dict):
        return None
    for key in ("year", "start_year", "end_year"):
        value = anchor.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def choose_candidate_pool(
    themed: list[dict[str, Any]],
    wider: list[dict[str, Any]],
    *,
    target: int,
) -> list[dict[str, Any]]:
    """Pick the pool the book is built from: on-theme, or the person's whole one.

    The readiness gate counts qualifying moments PERSON-WIDE, but the render
    fetched only the ones tagged to the tribute's theme and widened solely when
    that came back empty. A subject with nine qualifying memories and two tagged
    ones therefore passed the gate and got a two-memory book, while seven usable
    memories sat unread.

    On-theme stays preferred while it can carry the story; below ``target`` the
    person-wide pool wins, and it is a strict superset (same qualifying
    predicate, no theme join) so nothing on-theme is lost by widening.
    """
    if len(themed) >= target:
        return themed
    return wider if len(wider) > len(themed) else themed


def order_candidates_for_narrative(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Order the memories the way a story would tell them.

    The fetch returns them newest-EXTRACTED first, which is neither chronology
    nor the order the contributor told them -- so the assembler, asked to lay
    the pages along an arc, had nothing to sort by and the meeting could land
    halfway through the video. Reversing to oldest-extracted approximates the
    telling order, because an interview opens at the beginning.

    True chronology wins when it is actually available, but only when MOST of
    the pool carries a year: on live tributes 32 of 33 moments had no time
    anchor at all, and hoisting the single dated memory to page one would be
    worse than leaving the telling order alone.
    """
    told = list(reversed(candidates))
    years = [_anchor_year(m.get("time_anchor")) for m in told]
    dated = [y for y in years if y is not None]
    if len(dated) < max(2, (len(told) + 1) // 2):
        return told
    anchored = [(y, i, m) for i, (y, m) in enumerate(zip(years, told)) if y is not None]
    plain = [m for y, m in zip(years, told) if y is None]
    anchored.sort(key=lambda t: (t[0], t[1]))
    return [m for _y, _i, m in anchored] + plain


@dataclass(frozen=True)
class RenderContext:
    tribute_id: str
    person_id: str
    subject_name: str
    relationship: str | None
    gt_context: str
    video_put_url: str
    pdf_put_url: str
    poster_put_url: str = ""
    gender: str | None = None
    contributor_gender: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    message_text: str = ""
    archetype_leads: list[str] = field(default_factory=list)
    # Cumulative free-text adjustments the family asked for after seeing a
    # draft ("warmer", "more about his fishing trips"). Fed to the assembler
    # as <family_edit_requests>; shapes both captions and art directions.
    edit_instructions: list[str] = field(default_factory=list)
    n_pages: int = 15
    prime_photo_get_url: str = ""
    blend: str = "cream"
    transition: str = "bleed"
    fps: int = 30
    deage: bool = False
    composed_at: str = ""
    # Tribute CRM (spec 2026-07-14): composed voice directives + the pinned
    # visual style. Every default reproduces pre-CRM behavior so snapshots
    # written before migration 0039 render identically.
    style: dict[str, Any] | None = None
    profile_id: str = ""
    campaign_id: str = ""
    voice_block: str = ""
    opener_style: str = ""
    art_mood: str = ""
    narrative_block: str = ""
    fallback_opener: str = ""
    fallback_closing: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, tribute_id: str,
                  person_id: str) -> "RenderContext":
        return cls(
            tribute_id=tribute_id,
            person_id=person_id,
            subject_name=(d.get("subject_name") or ""),
            relationship=d.get("relationship"),
            gt_context=(d.get("gt_context") or ""),
            video_put_url=(d.get("video_put_url") or ""),
            pdf_put_url=(d.get("pdf_put_url") or ""),
            poster_put_url=(d.get("poster_put_url") or ""),
            gender=d.get("gender"),
            contributor_gender=d.get("contributor_gender"),
            candidates=list(d.get("candidates") or []),
            message_text=(d.get("message_text") or ""),
            archetype_leads=list(d.get("archetype_leads") or []),
            edit_instructions=list(d.get("edit_instructions") or []),
            n_pages=int(d.get("n_pages") or 15),
            prime_photo_get_url=(d.get("prime_photo_get_url") or ""),
            blend=(d.get("blend") or "cream"),
            transition=(d.get("transition") or "bleed"),
            fps=int(d.get("fps") or 30),
            deage=bool(d.get("deage") or False),
            composed_at=(d.get("composed_at") or ""),
            style=d.get("style") or None,
            profile_id=(d.get("profile_id") or ""),
            campaign_id=(d.get("campaign_id") or ""),
            voice_block=(d.get("voice_block") or ""),
            opener_style=(d.get("opener_style") or ""),
            art_mood=(d.get("art_mood") or ""),
            narrative_block=(d.get("narrative_block") or ""),
            fallback_opener=(d.get("fallback_opener") or ""),
            fallback_closing=(d.get("fallback_closing") or ""),
        )


def build_context_dict(
    *,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    candidates: list[dict[str, Any]],
    video_put_url: str,
    pdf_put_url: str,
    poster_put_url: str = "",
    gender: str | None = None,
    contributor_gender: str | None = None,
    message_text: str = "",
    archetype_leads: list[str] | None = None,
    edit_instructions: list[str] | None = None,
    n_pages: int = 15,
    prime_photo_get_url: str = "",
    blend: str = "cream",
    transition: str = "bleed",
    fps: int = 30,
    deage: bool = False,
    composed_at: str = "",
    style: dict[str, Any] | None = None,
    profile_id: str = "",
    campaign_id: str = "",
    voice_block: str = "",
    opener_style: str = "",
    art_mood: str = "",
    narrative_block: str = "",
    fallback_opener: str = "",
    fallback_closing: str = "",
) -> dict[str, Any]:
    """The dict stored under latest_generation_context['tribute_video']."""
    return {
        "subject_name": subject_name,
        "relationship": relationship,
        "gt_context": gt_context,
        "candidates": candidates,
        "video_put_url": video_put_url,
        "pdf_put_url": pdf_put_url,
        "poster_put_url": poster_put_url,
        "gender": gender,
        "contributor_gender": contributor_gender,
        "message_text": message_text,
        "archetype_leads": archetype_leads or [],
        "edit_instructions": edit_instructions or [],
        "n_pages": n_pages,
        "prime_photo_get_url": prime_photo_get_url,
        "blend": blend,
        "transition": transition,
        "fps": fps,
        "deage": deage,
        "composed_at": composed_at,
        "style": style,
        "profile_id": profile_id,
        "campaign_id": campaign_id,
        "voice_block": voice_block,
        "opener_style": opener_style,
        "art_mood": art_mood,
        "narrative_block": narrative_block,
        "fallback_opener": fallback_opener,
        "fallback_closing": fallback_closing,
    }
