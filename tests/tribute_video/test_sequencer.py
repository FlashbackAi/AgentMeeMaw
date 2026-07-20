from flashback.tribute_video.sequencer import DEFAULT_LAYOUT, assign_layouts

PALETTE = ["scrapbook", "type_over_crop", "split_duotone", "fullbleed_caption"]
PINS = {"opener": "split_duotone", "payoff": "type_over_crop", "closing": "fullbleed_caption"}


def test_pins_are_honored_at_their_positions():
    roles = ["opener", "beat", "beat", "beat", "payoff", "closing"]
    out = assign_layouts(roles, palette=PALETTE, pins=PINS)
    assert len(out) == len(roles)
    assert out[0] == "split_duotone"     # opener pin
    assert out[4] == "type_over_crop"    # payoff pin
    assert out[5] == "fullbleed_caption" # closing pin


def test_auto_beats_come_from_palette_with_no_immediate_repeat():
    roles = ["beat", "beat", "beat", "beat", "beat"]
    out = assign_layouts(roles, palette=PALETTE, pins={})
    assert all(slug in PALETTE for slug in out)
    assert all(out[i] != out[i + 1] for i in range(len(out) - 1))


def test_empty_palette_degrades_to_default():
    out = assign_layouts(["opener", "beat", "closing"], palette=[], pins={})
    assert out == [DEFAULT_LAYOUT, DEFAULT_LAYOUT, DEFAULT_LAYOUT]
