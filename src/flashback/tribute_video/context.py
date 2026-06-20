"""The render context the worker reads from Postgres.

Written by ``POST /tributes/{id}/generate`` into
``tributes.latest_generation_context['tribute_video']`` (Postgres authoritative;
the SQS message is a trigger only). Carries the assembled Book, the subject
descriptors, the Node-minted presigned URLs, and the render knobs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .book import Book, book_from_dict, book_to_dict

CONTEXT_KEY = "tribute_video"


@dataclass(frozen=True)
class RenderContext:
    tribute_id: str
    person_id: str
    subject_name: str
    relationship: str | None
    gt_context: str
    book: Book
    video_put_url: str
    pdf_put_url: str
    prime_photo_get_url: str = ""
    blend: str = "cream"
    transition: str = "bleed"
    fps: int = 30
    deage: bool = False
    composed_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, tribute_id: str,
                  person_id: str) -> "RenderContext":
        return cls(
            tribute_id=tribute_id,
            person_id=person_id,
            subject_name=(d.get("subject_name") or ""),
            relationship=d.get("relationship"),
            gt_context=(d.get("gt_context") or ""),
            book=book_from_dict(d.get("book") or {}),
            video_put_url=(d.get("video_put_url") or ""),
            pdf_put_url=(d.get("pdf_put_url") or ""),
            prime_photo_get_url=(d.get("prime_photo_get_url") or ""),
            blend=(d.get("blend") or "cream"),
            transition=(d.get("transition") or "bleed"),
            fps=int(d.get("fps") or 30),
            deage=bool(d.get("deage") or False),
            composed_at=(d.get("composed_at") or ""),
        )


def build_context_dict(
    *,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    book: Book,
    video_put_url: str,
    pdf_put_url: str,
    prime_photo_get_url: str = "",
    blend: str = "cream",
    transition: str = "bleed",
    fps: int = 30,
    deage: bool = False,
    composed_at: str = "",
) -> dict[str, Any]:
    """The dict stored under latest_generation_context['tribute_video']."""
    return {
        "subject_name": subject_name,
        "relationship": relationship,
        "gt_context": gt_context,
        "book": book_to_dict(book),
        "video_put_url": video_put_url,
        "pdf_put_url": pdf_put_url,
        "prime_photo_get_url": prime_photo_get_url,
        "blend": blend,
        "transition": transition,
        "fps": fps,
        "deage": deage,
        "composed_at": composed_at,
    }
