from flashback.ground_truth.render import render_ground_truth_block


def _gt(**kwargs):
    return {
        k: {"value": v, "provenance": "tap", "confidence": "high",
            "updated_at": "2026-06-11T12:00:00+00:00"}
        for k, v in kwargs.items()
    }


def test_empty_ground_truth_renders_empty_string():
    for audience in ("extraction", "portrait", "scene", "responder"):
        assert render_ground_truth_block({}, audience) == ""


def test_extraction_renders_line_per_known_field_only():
    out = render_ground_truth_block(
        _gt(region="Karimnagar, Telangana, India", birth_era="1950s"),
        "extraction",
    )
    assert "region: Karimnagar, Telangana, India" in out
    assert "birth_era: 1950s" in out
    assert "attire" not in out  # silent on unknowns — never "attire: unknown"


def test_portrait_renders_descriptor_fragments():
    out = render_ground_truth_block(
        _gt(
            region="Karimnagar, Telangana, India",
            birth_era="1950s",
            attire="cotton saree",
            distinctive_features=["glasses"],
            build="slight",
        ),
        "portrait",
    )
    assert "from Karimnagar, Telangana, India" in out
    assert "born in the 1950s" in out
    assert "typically wearing cotton saree" in out
    assert "glasses" in out
    assert "slight build" in out


def test_portrait_excludes_languages():
    out = render_ground_truth_block(_gt(languages=["Telugu"]), "portrait")
    assert out == ""


def test_scene_renders_single_setting_line():
    out = render_ground_truth_block(
        _gt(region="Karimnagar, Telangana, India",
            era_span=["1960s", "1970s"], setting_type="village"),
        "scene",
    )
    assert out.startswith("Setting context:")
    assert "Karimnagar" in out
    assert "1960s" in out
    assert "village" in out
