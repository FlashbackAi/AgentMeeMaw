from datetime import datetime, timezone

from flashback.ground_truth.store import apply_field

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_writes_new_field():
    out = apply_field(
        {}, field="region", value="Karimnagar, Telangana, India",
        provenance="inferred", confidence="high", now=NOW,
    )
    assert out is not None
    assert out["region"]["value"] == "Karimnagar, Telangana, India"
    assert out["region"]["provenance"] == "inferred"
    assert out["region"]["updated_at"] == NOW.isoformat()


def test_inferred_below_high_confidence_is_dropped():
    assert apply_field(
        {}, field="region", value="India",
        provenance="inferred", confidence="medium", now=NOW,
    ) is None


def test_lower_provenance_never_overwrites_higher():
    current = apply_field(
        {}, field="region", value="Karimnagar",
        provenance="tap", confidence="high", now=NOW,
    )
    assert apply_field(
        current, field="region", value="Mumbai",
        provenance="inferred", confidence="high", now=NOW,
    ) is None
    assert apply_field(
        current, field="region", value="Mumbai",
        provenance="onboarding", confidence="high", now=NOW,
    ) is None


def test_equal_provenance_refines():
    current = apply_field(
        {}, field="region", value="India",
        provenance="inferred", confidence="high", now=NOW,
    )
    out = apply_field(
        current, field="region", value="Karimnagar, Telangana, India",
        provenance="inferred", confidence="high", now=NOW,
    )
    assert out["region"]["value"] == "Karimnagar, Telangana, India"


def test_higher_provenance_overwrites():
    current = apply_field(
        {}, field="region", value="India",
        provenance="inferred", confidence="high", now=NOW,
    )
    out = apply_field(
        current, field="region", value="Karimnagar",
        provenance="user_edit", confidence="high", now=NOW,
    )
    assert out["region"]["provenance"] == "user_edit"


def test_list_fields_merge_union():
    current = apply_field(
        {}, field="distinctive_features", value="glasses",
        provenance="tap", confidence="high", now=NOW,
    )
    out = apply_field(
        current, field="distinctive_features", value=["mustache", "glasses"],
        provenance="tap", confidence="high", now=NOW,
    )
    assert out["distinctive_features"]["value"] == ["glasses", "mustache"]


def test_unknown_field_and_empty_value_rejected():
    assert apply_field({}, field="favourite_color", value="red",
                       provenance="tap", confidence="high", now=NOW) is None
    assert apply_field({}, field="region", value="  ",
                       provenance="tap", confidence="high", now=NOW) is None


def test_input_dict_not_mutated():
    current = {"region": {"value": "India", "provenance": "tap",
                          "confidence": "high", "updated_at": NOW.isoformat()}}
    apply_field(current, field="region", value="Karimnagar",
                provenance="user_edit", confidence="high", now=NOW)
    assert current["region"]["value"] == "India"
