"""Render a Book into MP4 + PDF via the Remotion project (spec §6).

Mirrors render.render_book's responsibility but delegates composition + motion
to Remotion. Reuses render._generate_illustrations so art generation stays DRY
(one code path, identical Gemini behavior). Pure orchestration: no DB/SQS/S3.

Layout ``palette``/``pins`` are the Recipe's control-panel levers (spec §5/§8).
Until the Recipe config is wired (later plan) they default to the proven
creative set; the worker will pass resolved values per campaign.
"""
from __future__ import annotations

import json
import os
import tempfile

import structlog
from PIL import Image

from . import style
from .props import build_props
from .remotion_cli import run_remotion
from .render import RenderResult, _generate_illustrations
from .stills_pdf import assemble_pdf_from_stills

log = structlog.get_logger("flashback.tribute_video.remotion_render")

# Proven default creative recipe (the Friendship-Day spike). The memorial /
# other recipes arrive via CRM config in a later plan.
DEFAULT_PALETTE = ["split_duotone", "scrapbook", "type_over_crop", "fullbleed_caption"]
DEFAULT_PINS = {"opener": "split_duotone", "payoff": "type_over_crop",
                "closing": "fullbleed_caption"}


def render_book_remotion(
    *, book, subject_name: str, relationship: str | None, gt_context: str,
    artist, pdf_path: str, mp4_path: str, poster_path: str | None = None,
    prime_photo: Image.Image | None = None, deage: bool = False,
    blend: str = "cream", fps: int = 30, concurrency: int = 4,
    kit: style.StyleKit | None = None, art_mood: str | None = None,
    palette: list[str] | None = None, pins: dict[str, str] | None = None,
    hold: float = 2.4, transition: float = 0.7, accent: str = "#e8552e",
) -> RenderResult:
    kit = kit or style.DEFAULT_KIT
    if not kit.generated_template:
        art_mood = None
    palette = palette if palette is not None else DEFAULT_PALETTE
    pins = pins if pins is not None else DEFAULT_PINS

    opener_illo, beat_illos, closing_illo = _generate_illustrations(
        artist=artist, book=book, subject_name=subject_name,
        relationship=relationship, gt_context=gt_context,
        prime_photo=prime_photo, deage=deage, blend=blend,
        concurrency=concurrency, art_mood=art_mood)

    with tempfile.TemporaryDirectory() as td:
        public_dir = os.path.join(td, "public")
        stills_dir = os.path.join(td, "stills")
        os.makedirs(public_dir, exist_ok=True)

        image_names: dict[str, str] = {"opener": "opener.png", "closing": "closing.png"}
        opener_illo.save(os.path.join(public_dir, "opener.png"))
        closing_illo.save(os.path.join(public_dir, "closing.png"))
        for i, illo in enumerate(beat_illos):
            name = f"beat_{i:03d}.png"
            illo.save(os.path.join(public_dir, name))
            image_names[f"beat_{i}"] = name

        props = build_props(book, kit=kit, image_names=image_names,
                            palette=palette, pins=pins, fps=fps, hold=hold,
                            transition=transition, accent=accent)
        props_path = os.path.join(td, "props.json")
        with open(props_path, "w", encoding="utf-8") as fh:
            json.dump(props, fh)

        run_remotion(props_path=props_path, public_dir=public_dir,
                     out_mp4=mp4_path, stills_dir=stills_dir)

        still_paths = sorted(
            os.path.join(stills_dir, f) for f in os.listdir(stills_dir)
            if f.endswith(".png"))
        pages = assemble_pdf_from_stills(still_paths, pdf_path, poster_path)

    log.info("tribute_render.remotion_done", pages=pages, scenes=len(props["scenes"]))
    return RenderResult(pages=pages, pdf_path=pdf_path, mp4_path=mp4_path,
                        poster_path=poster_path)
