"""Canonical universal-theme catalog assertions."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flashback.themes.universal import (
    UNIVERSAL_THEME_SLUGS,
    UNIVERSAL_THEMES,
    get_universal_theme,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
THEMES_MIGRATION = REPO_ROOT / "migrations" / "0020_themes.up.sql"


def test_five_universal_themes_exist() -> None:
    assert len(UNIVERSAL_THEMES) == 5


def test_universal_slugs_match_expected_set() -> None:
    assert UNIVERSAL_THEME_SLUGS == frozenset(
        {"family", "career", "friendships", "beliefs", "milestones"}
    )


def test_each_theme_has_nonempty_fields() -> None:
    for theme in UNIVERSAL_THEMES:
        assert theme.slug.strip()
        assert theme.display_name.strip()
        assert theme.description.strip()


def test_slugs_are_snake_case_ascii() -> None:
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for theme in UNIVERSAL_THEMES:
        assert pattern.match(theme.slug), f"non-canonical slug: {theme.slug!r}"


@pytest.mark.parametrize("slug", ["family", "career", "friendships", "beliefs", "milestones"])
def test_get_universal_theme_returns_each(slug: str) -> None:
    theme = get_universal_theme(slug)
    assert theme is not None
    assert theme.slug == slug


def test_get_universal_theme_returns_none_for_unknown() -> None:
    assert get_universal_theme("cricket") is None
    assert get_universal_theme("") is None


def test_migration_backfill_matches_python_catalog() -> None:
    """Drift detector: the 0020 backfill must seed exactly the same slugs
    and display names as the Python catalog. If either side changes
    without the other, this fails."""
    if not THEMES_MIGRATION.exists():
        pytest.skip("0020_themes.up.sql not present in this checkout")
    sql = THEMES_MIGRATION.read_text(encoding="utf-8")

    # Extract the VALUES list from the backfill INSERT.
    match = re.search(
        r"CROSS\s+JOIN\s+\(VALUES(.+?)\)\s+AS\s+u\(slug,\s*display_name\)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "could not locate universal-theme VALUES block in 0020"
    body = match.group(1)
    rows = re.findall(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", body)
    migration_pairs = {(slug, name) for slug, name in rows}

    python_pairs = {(t.slug, t.display_name) for t in UNIVERSAL_THEMES}
    assert migration_pairs == python_pairs, (
        f"drift: migration has {migration_pairs} vs python {python_pairs}"
    )
