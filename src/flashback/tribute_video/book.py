"""The page model the renderer consumes: an opener, ordered beats, a closing.

Produced by the assembler (``flashback.tribute.assembly`` storybook_video mode)
and rendered by ``flashback.tribute_video.render``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Beat:
    line: str                 # the 8-10 word page line
    art_direction: str        # visual brief for the illustration
    eyebrow: str = ""         # small-caps header (off in v1)
    moment_id: str = ""       # source moment ("" for opener/closing)
    # 2-4 word distilled title for typographic layouts (Big Type, Scrapbook,
    # Word Mask...). LLM-authored; props derives one from `line` when empty.
    display: str = ""


@dataclass(frozen=True)
class Book:
    cover_title: str
    opener: Beat
    beats: list[Beat]
    closing: Beat
    message: str = ""   # contributor's verbatim message (rendered near the end)


def _beat_to_dict(b: Beat) -> dict:
    return {"line": b.line, "art_direction": b.art_direction,
            "eyebrow": b.eyebrow, "moment_id": b.moment_id,
            "display": b.display}


def _beat_from_dict(d: dict) -> Beat:
    return Beat(
        line=(d.get("line") or ""),
        art_direction=(d.get("art_direction") or ""),
        eyebrow=(d.get("eyebrow") or ""),
        moment_id=(d.get("moment_id") or ""),
        display=(d.get("display") or ""),
    )


def book_to_dict(book: Book) -> dict:
    """Serialize for storage in tributes.latest_generation_context."""
    return {
        "cover_title": book.cover_title,
        "opener": _beat_to_dict(book.opener),
        "beats": [_beat_to_dict(b) for b in book.beats],
        "closing": _beat_to_dict(book.closing),
        "message": book.message,
    }


def book_from_dict(d: dict) -> Book:
    return Book(
        cover_title=(d.get("cover_title") or ""),
        opener=_beat_from_dict(d.get("opener") or {}),
        beats=[_beat_from_dict(b) for b in (d.get("beats") or [])],
        closing=_beat_from_dict(d.get("closing") or {}),
        message=(d.get("message") or ""),
    )
