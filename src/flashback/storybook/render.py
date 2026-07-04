"""Render a full storybook: cover + PAGE_COUNT template pages + the PDF.

Pure orchestration over the compositor + scene generators; no DB, no network
transfer (the worker downloads the anchor photo and uploads the outputs).
Failed panels are never silently shipped: they are collected in
``blank_panels`` and logged loudly so the caller can decide to retry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import structlog
from PIL import Image

# The PDF writer DCT-encodes pages via Image.SAVE["JPEG"] directly; make sure
# every save plugin is registered even when the process only touched PNGs.
Image.init()

from flashback.storybook.collections import PAGE_COUNT, Collection, asset_dir
from flashback.storybook.compose import (
    blend_chapter,
    expand_box,
    fill_panel,
    gemini_aspect,
    grid_boxes,
    grid_page_base,
    make_cover,
    overlay_chapter_text,
    panel_boxes,
)
from flashback.storybook.refs import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    MasterRefs,
    cast_rule,
)
from flashback.storybook.scenes import (
    gen_chapter_art,
    gen_cover_art,
    gen_scene,
)
from flashback.storybook.script import BookScript

log = structlog.get_logger("flashback.storybook.render")


@dataclass
class StorybookRenderResult:
    pdf_path: str
    cover_path: str
    page_paths: list[str] = field(default_factory=list)
    blank_panels: list[tuple[int, int]] = field(default_factory=list)


def render_storybook(
    *,
    script: BookScript,
    collection: Collection,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    master_refs: MasterRefs,
    gemini_client,
    verifier=None,
    out_dir: str,
    model: str = DEFAULT_GEMINI_IMAGE_MODEL,
) -> StorybookRenderResult:
    """Render every page + cover + PDF into ``out_dir``. Returns file paths
    plus the (page, panel) coordinates of any panel whose art failed."""
    os.makedirs(out_dir, exist_ok=True)
    adir = asset_dir(collection.slug)
    chapter = collection.layout == "chapter"
    role = relationship or "the subject"
    cast = cast_rule(script.characters, subject_name)

    cover_art = gen_cover_art(
        gemini_client,
        name=subject_name,
        relationship=relationship,
        gt_context=gt_context,
        ref=master_refs.for_stage(None),
        art_style=collection.art_style,
        model=model,
    )
    cover = make_cover(
        os.path.join(adir, "cover.png"),
        script.cover_title,
        subject_name,
        art=cover_art,
    )
    cover_path = os.path.join(out_dir, "page_00_cover.png")
    cover.save(cover_path)

    pages: list[Image.Image] = [cover]
    page_paths: list[str] = []
    blank: list[tuple[int, int]] = []

    for i in range(1, PAGE_COUNT + 1):
        tmpl_path = os.path.join(adir, f"{i}.png")
        tmpl = Image.open(tmpl_path).convert("RGB")
        panels = script.pages[i - 1].panels
        log.info(
            "storybook.render_page",
            collection=collection.slug,
            page=i,
            panels=len(panels),
        )
        if chapter:
            p = panels[0]
            art_box = expand_box(panel_boxes(tmpl_path)[0], *tmpl.size)
            art = gen_chapter_art(
                gemini_client,
                p.scene,
                master_refs.for_stage(p.age_stage),
                collection.art_style,
                gemini_aspect(art_box),
                subject=subject_name,
                role=role,
                cast=cast,
                model=model,
            )
            if art is None:
                blank.append((i, 1))
            page = blend_chapter(tmpl, art, art_box) if art else tmpl
            overlay_chapter_text(page, art_box, p.text)
        else:
            page = grid_page_base(tmpl, tmpl_path)
            boxes = grid_boxes(tmpl_path, len(panels))
            for pj, (p, box) in enumerate(zip(panels, boxes), 1):
                art = gen_scene(
                    gemini_client,
                    p.scene,
                    master_refs.for_stage(p.age_stage),
                    collection.art_style,
                    gemini_aspect(box),
                    text=p.text,
                    kind=p.kind,
                    subject=subject_name,
                    role=role,
                    cast=cast,
                    verifier=verifier,
                    model=model,
                )
                if art is not None:
                    fill_panel(page, art, box)
                else:
                    blank.append((i, pj))
        path = os.path.join(out_dir, f"page_{i:02d}.png")
        page.save(path)
        page_paths.append(path)
        pages.append(page)

    pdf_path = os.path.join(out_dir, f"storybook_{collection.slug}.pdf")
    pages[0].save(
        pdf_path, save_all=True, append_images=pages[1:], resolution=150.0
    )
    if blank:
        log.warning(
            "storybook.blank_panels",
            collection=collection.slug,
            count=len(blank),
            panels=blank,
        )
    return StorybookRenderResult(
        pdf_path=pdf_path,
        cover_path=cover_path,
        page_paths=page_paths,
        blank_panels=blank,
    )
