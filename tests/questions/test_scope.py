from flashback.questions.scope import (
    DEFAULT_SCOPE,
    PERSONAL,
    PRIVATE,
    PUBLIC,
    VALID_SCOPES,
    normalize_scope,
)


def test_valid_scopes_are_the_three_tiers():
    assert VALID_SCOPES == frozenset({PUBLIC, PERSONAL, PRIVATE})
    assert DEFAULT_SCOPE == PERSONAL


def test_normalize_keeps_valid_values_case_insensitively():
    assert normalize_scope("public") == "public"
    assert normalize_scope(" Private ") == "private"
    assert normalize_scope("PERSONAL") == "personal"


def test_normalize_defaults_to_personal_for_missing_or_unknown():
    assert normalize_scope(None) == "personal"
    assert normalize_scope("") == "personal"
    assert normalize_scope("secret") == "personal"
    assert normalize_scope(123) == "personal"
