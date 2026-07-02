"""Collections manifest — taxonomy, shipped template assets, public surface."""

from __future__ import annotations

import os

from flashback.storybook.collections import (
    COLLECTIONS,
    CURATED_SLUGS,
    PAGE_COUNT,
    asset_dir,
    public_collections,
)


def test_six_collections_registered() -> None:
    assert set(COLLECTIONS) == {
        "childhood",
        "interesting",
        "nostalgia",
        "festivals",
        "adventurous",
        "wisdom",
    }


def test_layout_and_tone_taxonomy() -> None:
    assert COLLECTIONS["wisdom"].layout == "chapter"
    assert all(COLLECTIONS[s].layout == "grid" for s in CURATED_SLUGS)
    assert {COLLECTIONS[s].tone for s in COLLECTIONS} <= {"gentle", "full"}
    # The two-tier content-safety split validated in the spike.
    assert COLLECTIONS["childhood"].tone == "gentle"
    assert COLLECTIONS["festivals"].tone == "gentle"
    assert COLLECTIONS["adventurous"].tone == "gentle"
    assert COLLECTIONS["interesting"].tone == "full"


def test_wisdom_is_not_curated() -> None:
    assert "wisdom" not in CURATED_SLUGS
    assert len(CURATED_SLUGS) == 5


def test_every_collection_ships_templates() -> None:
    for slug in COLLECTIONS:
        d = asset_dir(slug)
        assert os.path.exists(os.path.join(d, "cover.png")), slug
        for i in range(1, PAGE_COUNT + 1):
            assert os.path.exists(os.path.join(d, f"{i}.png")), (slug, i)


def test_manifests_are_subject_agnostic() -> None:
    """No legacy-specific names/motifs may ship in the product manifest."""
    for c in COLLECTIONS.values():
        blob = " ".join([c.display, c.theme_focus, c.signature_hint]).lower()
        assert "chandraiah" not in blob, c.slug
        assert "tatha" not in blob, c.slug
        assert "vinay" not in blob, c.slug


def test_public_surface_shape() -> None:
    rows = public_collections()
    assert len(rows) == 6
    assert {"slug", "display_name", "layout", "page_count"} <= set(rows[0])
    assert all(r["page_count"] == PAGE_COUNT for r in rows)
