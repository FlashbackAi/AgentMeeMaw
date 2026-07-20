from flashback.page_render.art import build_prompt


def test_scene_blend_is_full_bleed_not_paper():
    p = build_prompt("two friends on a rooftop", "urban India", "scene")
    low = p.lower()
    assert "edge to edge" in low
    assert "cream paper" not in low
    assert "no paper" in low


def test_cream_blend_keeps_paper_vignette():
    p = build_prompt("two friends on a rooftop", "urban India", "cream")
    assert "cream paper" in p.lower()


def test_green_blend_is_chroma_key():
    p = build_prompt("two friends on a rooftop", "urban India", "green")
    assert "chroma-key green" in p.lower()
