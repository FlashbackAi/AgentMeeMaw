"""Render a Book into a print PDF + an animated MP4.

Pure orchestration over compose + art + video: no DB, no SQS, no S3, no CLI. The
caller supplies a configured ``Artist``, the (optional) prime photo, and output
paths. The opener is a portrait from the prime photo when present; the
contributor message (if any) is its own calm page before the closing, reusing
the opener illustration as a visual bookend.

The character reference is generated first (the beats anchor to it), then the
opener + every beat + closing are illustrated CONCURRENTLY -- each is an
independent ~1-2 min Gemini call, so running them in a small thread pool turns
~18 serial calls into a handful of parallel batches. Pure scheduling: the model,
prompts, and reference are unchanged, so quality is identical to serial.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image

from . import compose, style, video
from .art import Artist
from .book import Beat, Book

DEFAULT_CONCURRENCY = 4


def _generate_illustrations(
    *,
    artist: Artist,
    book: Book,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    prime_photo: Image.Image | None,
    deage: bool,
    blend: str,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> tuple[Image.Image, list[Image.Image], Image.Image]:
    """Generate (opener, [beat...], closing) illustrations concurrently.

    The reference is built first (serial) so every beat anchors to the same
    figure; the independent page illustrations then run in a bounded thread
    pool. Order is preserved. A failed page propagates (the worker redrives).
    """
    reference = artist.character_reference(
        name=subject_name, relationship=relationship, gt_context=gt_context)

    def gen_opener() -> Image.Image:
        if prime_photo is not None:
            return artist.portrait_from_photo(
                prime_photo, name=subject_name, gt_context=gt_context,
                deage=deage, blend=blend)
        return artist.illustrate(book.opener.art_direction, gt_context, blend,
                                 reference=reference)

    def gen_beat(b: Beat) -> Image.Image:
        return artist.illustrate(b.art_direction, gt_context, blend,
                                 reference=reference)

    def gen_closing() -> Image.Image:
        return artist.illustrate(book.closing.art_direction, gt_context, blend,
                                 reference=reference)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        fut_opener = ex.submit(gen_opener)
        fut_beats = [ex.submit(gen_beat, b) for b in book.beats]
        fut_closing = ex.submit(gen_closing)
        # .result() re-raises any per-page GeminiError, failing the render.
        return (fut_opener.result(),
                [f.result() for f in fut_beats],
                fut_closing.result())


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
    concurrency: int = DEFAULT_CONCURRENCY,
) -> RenderResult:
    template = compose.load_template()
    template_rgba = template.convert("RGBA")

    opener_illo, beat_illos, closing_illo = _generate_illustrations(
        artist=artist, book=book, subject_name=subject_name,
        relationship=relationship, gt_context=gt_context,
        prime_photo=prime_photo, deage=deage, blend=blend,
        concurrency=concurrency)

    # Assemble pages in order; the message page reuses the opener illustration
    # as a visual bookend (no extra generation).
    ordered: list[tuple[str, Beat, Image.Image]] = []
    ordered.append(("opener", book.opener, opener_illo))
    for b, illo in zip(book.beats, beat_illos):
        ordered.append(("beat", b, illo))
    if book.message.strip():
        ordered.append(("message", Beat(line=book.message, art_direction=""),
                        opener_illo))
    ordered.append(("closing", book.closing, closing_illo))

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
