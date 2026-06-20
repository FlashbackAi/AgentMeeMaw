"""Render a Book into a print PDF + an animated MP4.

Pure orchestration over compose + art + video: no DB, no SQS, no S3, no CLI. The
caller supplies a configured ``Artist``, the (optional) prime photo, and output
paths. The opener is a portrait from the prime photo when present; the
contributor message (if any) is its own calm page before the closing, reusing
the opener illustration as a visual bookend.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from . import compose, style, video
from .art import Artist
from .book import Beat, Book


@dataclass(frozen=True)
class RenderResult:
    pages: int
    pdf_path: str
    mp4_path: str


def render_book(
    *,
    book: Book,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    artist: Artist,
    pdf_path: str,
    mp4_path: str,
    prime_photo: Image.Image | None = None,
    deage: bool = False,
    blend: str = "cream",
    transition: str = "bleed",
    fps: int = 30,
) -> RenderResult:
    template = compose.load_template()
    template_rgba = template.convert("RGBA")
    reference = artist.character_reference(
        name=subject_name, relationship=relationship, gt_context=gt_context)

    def illo_for(role: str, beat: Beat) -> Image.Image:
        if role == "opener" and prime_photo is not None:
            return artist.portrait_from_photo(
                prime_photo, name=subject_name, gt_context=gt_context,
                deage=deage, blend=blend)
        return artist.illustrate(beat.art_direction, gt_context, blend,
                                 reference=reference)

    # opener + beats (illustrate each); capture the opener illo for the message
    ordered: list[tuple[str, Beat, Image.Image]] = []
    opener_illo = illo_for("opener", book.opener)
    ordered.append(("opener", book.opener, opener_illo))
    for b in book.beats:
        ordered.append(("beat", b, illo_for("beat", b)))
    if book.message.strip():
        ordered.append(("message", Beat(line=book.message, art_direction=""),
                        opener_illo))
    ordered.append(("closing", book.closing, illo_for("closing", book.closing)))

    pages_img: list[Image.Image] = []
    video_pages: list[video.Page] = []
    for i, (role, beat, illo) in enumerate(ordered):
        layout = style.layout_for(role, i - 1)  # beat index 0-based
        illo_layer = compose.illustration_layer(
            template, illo, blend, layout.art_box, layout.art_valign)
        txt_layer = compose.text_layer(template, beat.eyebrow, beat.line,
                                       layout.text_box)
        page = Image.alpha_composite(
            Image.alpha_composite(template_rgba, illo_layer), txt_layer
        ).convert("RGB")
        pages_img.append(page)
        video_pages.append(video.Page(template, illo_layer, txt_layer))

    pages_img[0].save(pdf_path, save_all=True, append_images=pages_img[1:],
                      resolution=150.0)
    video.render_video(video_pages, mp4_path, fps=fps, transition=transition)
    return RenderResult(pages=len(pages_img), pdf_path=pdf_path, mp4_path=mp4_path)
