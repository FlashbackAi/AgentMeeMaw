from flashback.ground_truth.selection_llm import derive_anchor_chips


def test_anchor_chips_derived_from_birth_era():
    chips = derive_anchor_chips("1950s")
    assert chips == [
        "When they were young",
        "In the 1970s",
        "In the 1980s",
        "Later in life",
    ]


def test_anchor_chips_fallback_without_birth_era():
    chips = derive_anchor_chips(None)
    assert len(chips) == 4
    assert "Not sure" in chips


def test_anchor_chips_fallback_on_unparseable_era():
    assert "Not sure" in derive_anchor_chips("a while ago")
