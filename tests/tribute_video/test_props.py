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


def test_message_never_lands_on_a_display_only_layout():
    """The contributor's own words must survive into the video.

    Display-only layouts render the 2-4 word title, so a message routed to one
    was replaced by three words of itself (and clipped, at 96px in the orange
    block). It gets a caption layout from the palette instead.
    """
    from flashback.tribute_video.props import DISPLAY_ONLY_LAYOUTS

    long_message = ("Looking back, my favourite memories are of us first "
                    "meeting and growing close in the EWS lab.")
    # A palette of nothing but display-only layouts still can't strand it.
    for palette in (["split_duotone"], ["scrapbook", "type_over_crop"],
                    PALETTE, ["word_mask", "letter_note"]):
        props = build_props(_book(message=long_message), kit=DEFAULT_KIT,
                            image_names=NAMES, palette=palette, pins={})
        msg = next(s for s in props["scenes"] if s["role"] == "message")
        assert msg["layout_slug"] not in DISPLAY_ONLY_LAYOUTS, palette
        assert msg["text"] == long_message
        assert msg["display"] == ""  # nothing to distil the message down to
    # When the palette offers one, the admin's choice is honoured.
    props = build_props(_book(message=long_message), kit=DEFAULT_KIT,
                        image_names=NAMES, palette=["split_duotone", "magazine"],
                        pins={})
    msg = next(s for s in props["scenes"] if s["role"] == "message")
    assert msg["layout_slug"] == "magazine"


def test_labels_default_to_the_cover_title():
    """Layout chrome is occasion-owned, not hard-coded memorial copy."""
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                        palette=PALETTE, pins=PINS)
    labels = props["recipe"]["labels"]
    assert labels["chapter"] == "Aarav & Meera"
    assert labels["editorial"] == "Aarav & Meera"
    assert labels["stamp"] == "with love"
    # CRM config overrides field by field; unknown keys are ignored.
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                        palette=PALETTE, pins=PINS,
                        labels={"editorial": "A friendship, in pieces",
                                "bogus": "dropped"})
    labels = props["recipe"]["labels"]
    assert labels["editorial"] == "A friendship, in pieces"
    assert labels["chapter"] == "Aarav & Meera"
    assert "bogus" not in labels


def test_motion_preset_emitted_in_recipe():
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                        palette=PALETTE, pins=PINS, motion_preset="calm")
    assert props["recipe"]["motion_preset"] == "calm"


def test_scenes_carry_display_derived_when_absent():
    from flashback.tribute_video.props import derive_display

    # LLM-less book (no display on beats) -> code-side derivation kicks in
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                        palette=PALETTE, pins=PINS)
    for s in props["scenes"]:
        assert s["display"], s["role"]
        assert len(s["display"].split()) <= 3
    # derivation skips weak leading words and title-cases
    assert derive_display("She always kept the kitchen warm.") == "Always Kept Kitchen"
    assert derive_display("The chai at dawn") == "Chai Dawn"


def test_second_panel_is_a_neighbour_never_the_cover():
    """A two-image page pairs with adjacent art, not the cover portrait.

    "First image that isn't this one" resolved to the cover on every page, so
    a scrapbook, a filmstrip and a gallery wall in the same deck all showed the
    same face beside text about something else.
    """
    names = {"opener": "opener.png", "closing": "closing.png",
             "beat_0": "beat_000.png", "beat_1": "beat_001.png",
             "beat_2": "beat_002.png"}
    book = Book(
        cover_title="Aarav & Meera",
        opener=Beat(line="How we met", art_direction="a"),
        beats=[Beat(line=f"beat {i}", art_direction="b", moment_id=f"m{i}")
               for i in range(3)],
        closing=Beat(line="to the next chapter", art_direction="d"),
        message="",
    )
    for slug in ("scrapbook", "filmstrip", "gallery_wall"):
        props = build_props(book, kit=DEFAULT_KIT, image_names=names,
                            palette=[slug], pins={})
        scenes = props["scenes"]
        page_images = [s["image"] for s in scenes]
        for i, s in enumerate(scenes):
            if s["layout_slug"] != slug:
                continue
            assert s["image2"] != s["image"], (slug, i)
            if s["role"] != "opener":
                assert s["image2"] != "opener.png", (slug, i)
            # and it comes from nearby, not an arbitrary corner of the deck
            assert abs(page_images.index(s["image2"]) - i) <= 3, (slug, i)


def test_multi_image_scenes_get_distinct_second_image():
    # force each multi-image layout via a single-slug palette for the auto beats
    for slug in ("scrapbook", "filmstrip", "gallery_wall"):
        props = build_props(_book(), kit=DEFAULT_KIT, image_names=NAMES,
                            palette=[slug], pins={})
        multi = [s for s in props["scenes"] if s["layout_slug"] == slug]
        assert multi, slug  # at least one
        for s in multi:
            assert s.get("image2") and s["image2"] != s["image"]
