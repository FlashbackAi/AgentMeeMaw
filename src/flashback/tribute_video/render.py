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

import structlog
from PIL import Image

from . import compose, style, video
from .art import Artist, GeminiError
from .book import Beat, Book

log = structlog.get_logger("flashback.tribute_video.render")

DEFAULT_CONCURRENCY = 4
# Sentinel: when the caller passes nothing, use the bundled backing track. A
# caller can pass audio_path=None explicitly to render a silent video.
_DEFAULT_AUDIO = object()


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
    art_mood: str | None = None,
    aspect: str | None = None,
    subject_gender: str | None = None,
    contributor_gender: str | None = None,
) -> tuple[Image.Image, list[Image.Image], Image.Image]:
    """Generate (opener, [beat...], closing) illustrations concurrently.

    The reference is built first (serial) so every beat anchors to the same
    figure; the independent page illustrations then run in a bounded thread
    pool. Order is preserved. A failed SCENE propagates (the worker redrives);
    a refused real-photo COVER falls back to the illustrated opener instead,
    so one likeness refusal can't strand the whole tribute in 'failed'.
    """
    reference = artist.character_reference(
        name=subject_name, relationship=relationship, gt_context=gt_context,
        gender=subject_gender)

    def _illustrated_opener() -> Image.Image:
        return artist.illustrate(book.opener.art_direction, gt_context, blend,
                                 reference=reference, art_mood=art_mood,
                                 aspect=aspect, subject_gender=subject_gender,
                                 contributor_gender=contributor_gender)

    def gen_opener() -> Image.Image:
        if prime_photo is not None:
            try:
                return artist.portrait_from_photo(
                    prime_photo, name=subject_name, gt_context=gt_context,
                    deage=deage, blend=blend, gender=subject_gender)
            except GeminiError as exc:
                # Gemini refuses to repaint some real photos (likeness filter).
                # The cover is the ONLY page that reproduces a real face; rather
                # than let one refused portrait strand the whole tribute in
                # 'failed', fall back to the illustrated opener (figure from
                # behind, no face -- the same safe path every scene uses).
                log.warning("tribute_render.cover_likeness_refused",
                            error=str(exc)[:200])
                return _illustrated_opener()
        return _illustrated_opener()

    def gen_beat(b: Beat) -> Image.Image:
        return artist.illustrate(b.art_direction, gt_context, blend,
                                 reference=reference, art_mood=art_mood,
                                 aspect=aspect, subject_gender=subject_gender,
                                 contributor_gender=contributor_gender)

    def gen_closing() -> Image.Image:
        return artist.illustrate(book.closing.art_direction, gt_context, blend,
                                 reference=reference, art_mood=art_mood,
                                 aspect=aspect, subject_gender=subject_gender,
                                 contributor_gender=contributor_gender)

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
    poster_path: str | None = None


def render_book(
    *,
    book: Book,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    artist: Artist,
    pdf_path: str,
    mp4_path: str,
    poster_path: str | None = None,
    prime_photo: Image.Image | None = None,
    deage: bool = False,
    blend: str = "cream",
    transition: str = "bleed",
    fps: int = 30,
    concurrency: int = DEFAULT_CONCURRENCY,
    audio_path: str | None = _DEFAULT_AUDIO,  # type: ignore[assignment]
    kit: style.StyleKit | None = None,
    art_mood: str | None = None,
    subject_gender: str | None = None,
    contributor_gender: str | None = None,
) -> RenderResult:
    kit = kit or style.DEFAULT_KIT
    template = compose.load_template(kit)
    template_rgba = template.convert("RGBA")

    # The new-look levers (themed paint mood, halo-tight crop, border-safe
    # zones) ride ONLY on CRM-generated templates; a render on the shipped
    # template — Father's Day above all — stays byte-identical.
    if not kit.generated_template:
        art_mood = None

    opener_illo, beat_illos, closing_illo = _generate_illustrations(
        artist=artist, book=book, subject_name=subject_name,
        relationship=relationship, gt_context=gt_context,
        prime_photo=prime_photo, deage=deage, blend=blend,
        concurrency=concurrency, art_mood=art_mood,
        subject_gender=subject_gender, contributor_gender=contributor_gender)

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
        if kit.generated_template:
            layout = style.safe_layout(layout)
        line = beat.line
        if role == "message":
            # User-authored free text: escalate the layout instead of
            # letting a long message spill over the border/art.
            layout, include_art, line = compose.plan_message_page(
                template, line, layout, kit=kit)
            if not include_art:
                illo = None
        illo_layer = compose.illustration_layer(
            template, illo, blend, layout.art_box, layout.art_valign,
            tight_crop=kit.generated_template)
        txt_layer = compose.text_layer(template, beat.eyebrow, line,
                                       layout.text_box, kit=kit)
        page = Image.alpha_composite(
            Image.alpha_composite(template_rgba, illo_layer), txt_layer
        ).convert("RGB")
        pages_img.append(page)
        video_pages.append(video.Page(template, illo_layer, txt_layer))

    pages_img[0].save(pdf_path, save_all=True, append_images=pages_img[1:],
                      resolution=150.0)
    # The opener page IS the cover (portrait + title). Save it as a standalone
    # poster JPEG so the card/thumbnail surfaces the cover instead of a stray
    # video frame -- the worker PUTs it to the Node-minted poster URL.
    if poster_path is not None:
        pages_img[0].save(poster_path, format="JPEG", quality=88)
    track = kit.audio_path if audio_path is _DEFAULT_AUDIO else audio_path
    video.render_video(video_pages, mp4_path, fps=fps, transition=transition,
                       audio_path=track)
    return RenderResult(pages=len(pages_img), pdf_path=pdf_path, mp4_path=mp4_path,
                        poster_path=poster_path)
