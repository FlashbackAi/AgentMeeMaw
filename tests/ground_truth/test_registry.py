from flashback.ground_truth.registry import (
    ASKABLE_KEYS,
    INFERRABLE_KEYS,
    REGISTRY,
    REGISTRY_BY_KEY,
)


def test_registry_has_all_nine_fields_in_priority_order():
    assert [f.key for f in REGISTRY] == [
        "region",
        "birth_era",
        "setting_type",
        "attire",
        "distinctive_features",
        "build",
        "cultural_context",
        "era_span",
        "languages",
    ]


def test_askable_excludes_inferred_only_and_derived_fields():
    assert "cultural_context" not in ASKABLE_KEYS
    assert "era_span" not in ASKABLE_KEYS
    assert "region" in ASKABLE_KEYS
    assert "languages" in ASKABLE_KEYS


def test_inferrable_excludes_only_era_span():
    assert "era_span" not in INFERRABLE_KEYS
    assert "cultural_context" in INFERRABLE_KEYS
    assert "region" in INFERRABLE_KEYS


def test_registry_by_key_roundtrip():
    assert REGISTRY_BY_KEY["attire"].value_type == "text"
    assert REGISTRY_BY_KEY["distinctive_features"].value_type == "list"
    assert REGISTRY_BY_KEY["languages"].value_type == "list"
    assert REGISTRY_BY_KEY["era_span"].value_type == "list"
