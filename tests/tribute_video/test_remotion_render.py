import json
import os

from PIL import Image

from flashback.tribute_video import remotion_render
from flashback.tribute_video.book import Beat, Book


def _book():
    return Book(cover_title="Aarav & Meera",
                opener=Beat(line="How we met", art_direction="a"),
                beats=[Beat(line="still ride or die", art_direction="b", moment_id="m1")],
                closing=Beat(line="to the next chapter", art_direction="c"))


def _img():
    return Image.new("RGB", (8, 8), (120, 90, 60))


def test_render_produces_pdf_mp4_poster(monkeypatch, tmp_path):
    # Fake art generation (no Gemini) and the Remotion subprocess (no Node).
    monkeypatch.setattr(remotion_render, "_generate_illustrations",
                        lambda **k: (_img(), [_img()], _img()))

    def fake_run(*, props_path, public_dir, out_mp4, stills_dir, **k):
        os.makedirs(stills_dir, exist_ok=True)
        with open(out_mp4, "wb") as fh:
            fh.write(b"\x00")
        scenes = json.load(open(props_path, encoding="utf-8"))["scenes"]
        # every scene image referenced must exist in the public dir
        for sc in scenes:
            assert os.path.exists(os.path.join(public_dir, sc["image"]))
        for i in range(len(scenes)):
            Image.new("RGB", (896, 1600), (i, i, i)).save(
                os.path.join(stills_dir, f"scene_{i:03d}.png"))

    monkeypatch.setattr(remotion_render, "run_remotion", fake_run)

    res = remotion_render.render_book_remotion(
        book=_book(), subject_name="Aarav", relationship="best friend",
        gt_context="", artist=None,
        pdf_path=str(tmp_path / "o.pdf"), mp4_path=str(tmp_path / "o.mp4"),
        poster_path=str(tmp_path / "o.poster.jpg"))

    assert res.pages == 3  # opener + 1 (payoff) beat + closing
    assert os.path.exists(res.pdf_path) and os.path.getsize(res.pdf_path) > 0
    assert os.path.exists(res.mp4_path)
    assert res.poster_path and os.path.exists(res.poster_path)


def test_recipe_kwargs_defaults_when_style_absent():
    kw = remotion_render.recipe_kwargs_from_style(None)
    assert kw["palette"] == remotion_render.DEFAULT_PALETTE
    assert kw["pins"] == remotion_render.DEFAULT_PINS
    assert kw["hold"] == remotion_render.DEFAULT_HOLD
    assert kw["transition"] == remotion_render.DEFAULT_TRANSITION
    assert kw["accent"] == remotion_render.DEFAULT_ACCENT


def test_recipe_kwargs_reads_snapshot_style():
    style = {
        "recipe": {
            "layout_palette": ["framed_hero", "fullbleed_caption"],
            "layout_pins": {"opener": "framed_hero"},
            "pacing": {"hold": 3.4, "transition": 1.1},
        },
        "ink": {"accent": "#123456"},
    }
    kw = remotion_render.recipe_kwargs_from_style(style)
    assert kw["palette"] == ["framed_hero", "fullbleed_caption"]
    assert kw["pins"] == {"opener": "framed_hero"}
    assert kw["hold"] == 3.4 and kw["transition"] == 1.1
    assert kw["accent"] == "#123456"
