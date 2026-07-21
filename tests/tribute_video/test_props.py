from flashback.tribute_video.book import Beat, Book
from flashback.tribute_video.props import build_props
from flashback.tribute_video.style import DEFAULT_KIT

PALETTE = ["scrapbook", "type_over_crop", "split_duotone", "fullbleed_caption"]
PINS = {"opener": "split_duotone", "payoff": "type_over_crop", "closing": "fullbleed_caption"}
NAMES = {"opener": "opener.png", "closing": "closing.png",
         "beat_0": "beat_000.png", "beat_1": "beat_001.png"}


def _book(message=""):
    return Book(
        cover_title="Aarav & Meera",
        opener=Beat(line="How we met", art_direction="a"),
        beats=[Beat(line="best chaos ever", art_direction="b", moment_id="m1"),
               Beat(line="still ride or die", art_direction="c", moment_id="m2")],
        closing=Beat(line="to the next chapter", art_direction="d"),
        message=message,
    )


def test_contract_shape_and_pins():
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                        palette=PALETTE, pins=PINS)
    assert props["meta"]["width"] == 896 and props["meta"]["height"] == 1600
    assert props["recipe"]["ink"]["accent"]  # accent present
    assert props["recipe"]["fonts"]["main_family"] == "Playfair Display"
    roles = [s["role"] for s in props["scenes"]]
    assert roles == ["opener", "beat", "payoff", "closing"]  # last beat = payoff
    layouts = [s["layout_slug"] for s in props["scenes"]]
    assert layouts[0] == "split_duotone"      # opener pin
    assert layouts[2] == "type_over_crop"     # payoff pin (last beat)
    assert layouts[3] == "fullbleed_caption"  # closing pin
    assert layouts[1] in PALETTE              # auto beat


def test_message_scene_reuses_opener_image():
    props = build_props(_book(message="you're my person."), kit=DEFAULT_KIT,
                        image_names=NAMES, palette=PALETTE, pins=PINS)
    roles = [s["role"] for s in props["scenes"]]
    assert roles == ["opener", "beat", "payoff", "message", "closing"]
    msg = next(s for s in props["scenes"] if s["role"] == "message")
    assert msg["text"] == "you're my person."
    assert msg["image"] == "opener.png"


def test_motion_preset_emitted_in_recipe():
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                        palette=PALETTE, pins=PINS, motion_preset="calm")
    assert props["recipe"]["motion_preset"] == "calm"


def test_multi_image_scenes_get_distinct_second_image():
    # force each multi-image layout via a single-slug palette for the auto beats
    for slug in ("scrapbook", "filmstrip", "gallery_wall"):
        props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                            palette=[slug], pins={})
        multi = [s for s in props["scenes"] if s["layout_slug"] == slug]
        assert multi, slug  # at least one
        for s in multi:
            assert s.get("image2") and s["image2"] != s["image"]
