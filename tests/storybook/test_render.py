"""Renderer — full-book orchestration over the real shipped templates,
with all model calls stubbed."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from PIL import Image

from flashback.storybook.collections import COLLECTIONS, PAGE_COUNT
from flashback.storybook.refs import MasterRefs, PRIMARY_STAGE
from flashback.storybook.render import render_storybook
from flashback.storybook.script import BookScript


def _script(panels: int) -> BookScript:
    return BookScript.from_dict(
        {
            "cover_title": "A Test Book",
            "characters": [
                {"name": "Mokshith", "who": "his son",
                 "appearance": "short black hair, slim"}
            ],
            "pages": [
                {
                    "panels": [
                        {
                            "scene": "a scene",
                            "text": "a caption",
                            "kind": "caption",
                            "age_stage": "mid",
                        }
                    ]
                    * panels
                }
            ]
            * PAGE_COUNT,
        }
    )


def _refs() -> MasterRefs:
    m = MasterRefs()
    m._refs = {PRIMARY_STAGE: Image.new("RGB", (8, 8))}
    return m


_ART = Image.new("RGB", (320, 180), (90, 70, 50))


def test_grid_book_renders_cover_pages_and_pdf(tmp_path) -> None:
    with patch(
        "flashback.storybook.render.gen_scene", return_value=_ART
    ), patch(
        "flashback.storybook.render.gen_cover_art", return_value=_ART
    ):
        out = render_storybook(
            script=_script(3),
            collection=COLLECTIONS["childhood"],
            subject_name="Subject",
            relationship="Grand Father",
            gt_context="gt",
            master_refs=_refs(),
            gemini_client=MagicMock(),
            out_dir=str(tmp_path),
        )
    assert os.path.exists(out.cover_path)
    assert len(out.page_paths) == PAGE_COUNT
    assert all(os.path.exists(p) for p in out.page_paths)
    assert os.path.exists(out.pdf_path)
    assert out.blank_panels == []


def test_grid_book_passes_cast_to_every_panel(tmp_path) -> None:
    with patch(
        "flashback.storybook.render.gen_scene", return_value=_ART
    ) as g, patch(
        "flashback.storybook.render.gen_cover_art", return_value=_ART
    ):
        render_storybook(
            script=_script(3),
            collection=COLLECTIONS["childhood"],
            subject_name="Subject",
            relationship="Grand Father",
            gt_context="gt",
            master_refs=_refs(),
            gemini_client=MagicMock(),
            out_dir=str(tmp_path),
        )
    for call in g.call_args_list:
        assert "Mokshith (his son): short black hair, slim" in call.kwargs[
            "cast"
        ]


def test_chapter_book_passes_cast(tmp_path) -> None:
    with patch(
        "flashback.storybook.render.gen_chapter_art", return_value=_ART
    ) as g, patch(
        "flashback.storybook.render.gen_cover_art", return_value=_ART
    ):
        render_storybook(
            script=_script(1),
            collection=COLLECTIONS["wisdom"],
            subject_name="Subject",
            relationship=None,
            gt_context="gt",
            master_refs=_refs(),
            gemini_client=MagicMock(),
            out_dir=str(tmp_path),
        )
    for call in g.call_args_list:
        assert "Mokshith" in call.kwargs["cast"]


def test_chapter_book_renders(tmp_path) -> None:
    with patch(
        "flashback.storybook.render.gen_chapter_art", return_value=_ART
    ), patch(
        "flashback.storybook.render.gen_cover_art", return_value=_ART
    ):
        out = render_storybook(
            script=_script(1),
            collection=COLLECTIONS["wisdom"],
            subject_name="Subject",
            relationship=None,
            gt_context="gt",
            master_refs=_refs(),
            gemini_client=MagicMock(),
            out_dir=str(tmp_path),
        )
    assert len(out.page_paths) == PAGE_COUNT
    assert os.path.exists(out.pdf_path)
    assert out.blank_panels == []


def test_failed_panel_is_reported_not_silent(tmp_path) -> None:
    results = [None] + [_ART] * (PAGE_COUNT * 3 - 1)
    with patch(
        "flashback.storybook.render.gen_scene", side_effect=results
    ), patch(
        "flashback.storybook.render.gen_cover_art", return_value=_ART
    ):
        out = render_storybook(
            script=_script(3),
            collection=COLLECTIONS["childhood"],
            subject_name="S",
            relationship=None,
            gt_context="",
            master_refs=_refs(),
            gemini_client=MagicMock(),
            out_dir=str(tmp_path),
            concurrency=1,  # ordered side_effect needs deterministic calls
        )
    assert out.blank_panels == [(1, 1)]
    assert len(out.page_paths) == PAGE_COUNT  # book still completes


def _child_script(panels: int) -> BookScript:
    d = _script(panels).to_dict()
    for page in d["pages"]:
        for p in page["panels"]:
            p["age_stage"] = "child"
    return BookScript.from_dict(d)


def test_cover_anchors_to_dominant_age_stage(tmp_path) -> None:
    child_ref = Image.new("RGB", (8, 8), (1, 1, 1))
    mid_ref = Image.new("RGB", (8, 8), (2, 2, 2))
    m = MasterRefs()
    m._refs = {"child": child_ref, PRIMARY_STAGE: mid_ref}
    with patch(
        "flashback.storybook.render.gen_scene", return_value=_ART
    ), patch(
        "flashback.storybook.render.gen_cover_art", return_value=_ART
    ) as c:
        render_storybook(
            script=_child_script(3),
            collection=COLLECTIONS["childhood"],
            subject_name="Subject",
            relationship="Grand Father",
            gt_context="gt",
            master_refs=m,
            gemini_client=MagicMock(),
            out_dir=str(tmp_path),
            gender="male",
        )
    kwargs = c.call_args.kwargs
    # A childhood-dominated book gets a childhood cover, not the 'mid' primary.
    assert kwargs["ref"] is child_ref
    assert kwargs["age"] == "a boy about ten years old"
