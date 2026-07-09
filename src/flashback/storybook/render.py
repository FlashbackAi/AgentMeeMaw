"""Render a full storybook: cover + PAGE_COUNT template pages + the PDF.

Pure orchestration over the compositor + scene generators; no DB, no network
transfer (the worker downloads the anchor photo and uploads the outputs).
Failed panels are never silently shipped: they are collected in
``blank_panels`` and logged loudly so the caller can decide to retry.

Art generation runs CONCURRENTLY (tribute render pattern): every panel + the
cover is an independent Gemini call conditioned only on the prebuilt master
refs, so a bounded thread pool turns ~22 serial calls into a handful of
parallel batches. Pure scheduling — the model, prompts, refs, and verifier
loop are unchanged, so output quality is identical to the serial render.
Composition stays strictly in page order.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
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
    age_descriptor,
    cast_rule,
)
from flashback.storybook.scenes import (
    gen_chapter_art,
    gen_cover_art,
    gen_scene,
)
from flashback.storybook.script import BookScript, dominant_age_stage

log = structlog.get_logger("flashback.storybook.render")

DEFAULT_CONCURRENCY = 4


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
    gender: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> StorybookRenderResult:
    """Render every page + cover + PDF into ``out_dir``. Returns file paths
    plus the (page, panel) coordinates of any panel whose art failed."""
    os.makedirs(out_dir, exist_ok=True)
    adir = asset_dir(collection.slug)
    chapter = collection.layout == "chapter"
    role = relationship or "the subject"
    cast = cast_rule(script.characters, subject_name)
    # The cover shows the subject at the life stage the book mostly shows —
    # anchoring it to the matching ref (not always the 'mid' primary) is what
    # keeps a childhood book from wearing an old man on its cover.
    cover_stage = dominant_age_stage(script)

    # --- plan: template math + one generation closure per panel -------------
    def _scene_job(p, box):
        ref = master_refs.for_stage(p.age_stage)
        aspect = gemini_aspect(box)
        return lambda: gen_scene(
            gemini_client,
            p.scene,
            ref,
            collection.art_style,
            aspect,
            text=p.text,
            kind=p.kind,
            subject=subject_name,
            role=role,
            cast=cast,
            verifier=verifier,
            model=model,
        )

    def _chapter_job(p, art_box):
        ref = master_refs.for_stage(p.age_stage)
        aspect = gemini_aspect(art_box)
        return lambda: gen_chapter_art(
            gemini_client,
            p.scene,
            ref,
            collection.art_style,
            aspect,
            subject=subject_name,
            role=role,
            cast=cast,
            model=model,
        )

    def _cover_job():
        return gen_cover_art(
            gemini_client,
            name=subject_name,
            relationship=relationship,
            gt_context=gt_context,
            ref=master_refs.for_stage(cover_stage),
            art_style=collection.art_style,
            age=age_descriptor(cover_stage, gender),
            model=model,
        )

    plans = []  # (page_index, tmpl, tmpl_path, boxes_or_art_box, panels)
    jobs: dict[tuple[int, int], object] = {}
    for i in range(1, PAGE_COUNT + 1):
        tmpl_path = os.path.join(adir, f"{i}.png")
        tmpl = Image.open(tmpl_path).convert("RGB")
        panels = script.pages[i - 1].panels
        if chapter:
            art_box = expand_box(panel_boxes(tmpl_path)[0], *tmpl.size)
            jobs[(i, 1)] = _chapter_job(panels[0], art_box)
            plans.append((i, tmpl, tmpl_path, art_box, panels))
        else:
            boxes = grid_boxes(tmpl_path, len(panels))
            for pj, (p, box) in enumerate(zip(panels, boxes), 1):
                jobs[(i, pj)] = _scene_job(p, box)
            plans.append((i, tmpl, tmpl_path, boxes, panels))

    # --- generate: cover + every panel through a bounded pool ---------------
    log.info(
        "storybook.render_generating",
        collection=collection.slug,
        cover_stage=cover_stage,
        panels=len(jobs),
        concurrency=concurrency,
    )
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        fut_cover = ex.submit(_cover_job)
        futs = {key: ex.submit(job) for key, job in jobs.items()}
        cover_art = fut_cover.result()
        art = {key: f.result() for key, f in futs.items()}

    # --- compose: strictly in page order (pixel path unchanged) -------------
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

    for i, tmpl, tmpl_path, geometry, panels in plans:
        log.info(
            "storybook.render_page",
            collection=collection.slug,
            page=i,
            panels=len(panels),
        )
        if chapter:
            art_box = geometry
            page_art = art.get((i, 1))
            if page_art is None:
                blank.append((i, 1))
            page = blend_chapter(tmpl, page_art, art_box) if page_art else tmpl
            overlay_chapter_text(page, art_box, panels[0].text)
        else:
            page = grid_page_base(tmpl, tmpl_path)
            for pj, box in enumerate(geometry, 1):
                panel_art = art.get((i, pj))
                if panel_art is not None:
                    fill_panel(page, panel_art, box)
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
