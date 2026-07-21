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
# other recipes arrive via CRM config; missing config degrades here so a render
# never blocks (invariant).
DEFAULT_PALETTE = ["split_duotone", "scrapbook", "type_over_crop", "fullbleed_caption"]
DEFAULT_PINS = {"opener": "split_duotone", "payoff": "type_over_crop",
                "closing": "fullbleed_caption"}
DEFAULT_ACCENT = "#e8552e"
DEFAULT_HOLD = 2.4
DEFAULT_TRANSITION = 0.7
# The proven Friendship spike is punchy; memorial/other themes override via CRM.
DEFAULT_MOTION_PRESET = "punchy"

# Remotion always paints full-bleed edge-to-edge art (no paper margin) at a
# portrait aspect that fills the 9:16 frame with minimal cropping.
SCENE_BLEND = "scene"
SCENE_ASPECT = "3:4"


def recipe_kwargs_from_style(style: dict | None) -> dict:
    """Extract the Remotion render's Recipe levers from the snapshot ``style``.

    The CRM visual-theme snapshot carries ``recipe`` (layout palette/pins +
    pacing) and ``ink.accent``. Every field defaults to the proven creative
    values, so a pre-Recipe snapshot (or malformed config) still renders
    (spec §5, config-never-blocks invariant). Returns kwargs for
    ``render_book_remotion``.
    """
    style = style or {}
    recipe = style.get("recipe") or {}
    pacing = recipe.get("pacing") or {}
    accent = (style.get("ink") or {}).get("accent") or recipe.get("accent") or DEFAULT_ACCENT
    return {
        "palette": list(recipe.get("layout_palette") or DEFAULT_PALETTE),
        "pins": dict(recipe.get("layout_pins") or DEFAULT_PINS),
        "hold": float(pacing.get("hold", DEFAULT_HOLD)),
        "transition": float(pacing.get("transition", DEFAULT_TRANSITION)),
        "accent": accent,
        "motion_preset": recipe.get("motion_preset") or DEFAULT_MOTION_PRESET,
    }


def render_book_remotion(
    *, book, subject_name: str, relationship: str | None, gt_context: str,
    artist, pdf_path: str, mp4_path: str, poster_path: str | None = None,
    prime_photo: Image.Image | None = None, deage: bool = False,
    blend: str = "cream", fps: int = 30, concurrency: int = 4,
    kit: style.StyleKit | None = None, art_mood: str | None = None,
    palette: list[str] | None = None, pins: dict[str, str] | None = None,
    hold: float = 2.4, transition: float = 0.7, accent: str = "#e8552e",
    motion_preset: str = DEFAULT_MOTION_PRESET,
) -> RenderResult:
    kit = kit or style.DEFAULT_KIT
    if not kit.generated_template:
        art_mood = None
    palette = palette if palette is not None else DEFAULT_PALETTE
    pins = pins if pins is not None else DEFAULT_PINS

    # Remotion composites art into its OWN layout backgrounds, so the art is
    # always full-bleed "scene" mode (no cream paper margin to crop) and painted
    # at a portrait aspect that fills the vertical frame. The incoming ``blend``
    # (a legacy Pillow concept) is intentionally ignored here.
    opener_illo, beat_illos, closing_illo = _generate_illustrations(
        artist=artist, book=book, subject_name=subject_name,
        relationship=relationship, gt_context=gt_context,
        prime_photo=prime_photo, deage=deage, blend=SCENE_BLEND,
        concurrency=concurrency, art_mood=art_mood, aspect=SCENE_ASPECT)

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
                            transition=transition, accent=accent,
                            motion_preset=motion_preset)
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
