"""Curation — one Sonnet pass splits the pool; code enforces single-assignment."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from flashback.storybook.collections import CURATED_SLUGS
from flashback.storybook.curation import curate_moments, dedupe_assignments

_SETTINGS = SimpleNamespace(
    llm_big_provider="anthropic", llm_big_model="claude-sonnet-4-6"
)


def test_moment_in_two_collections_keeps_best_rank() -> None:
    # 9 sits at rank 0 in festivals but rank 1 in childhood -> festivals wins.
    raw = {"childhood": [5, 9], "festivals": [9, 2]}
    out = dedupe_assignments(raw)
    assert 9 in out["festivals"]
    assert 9 not in out["childhood"]
    assert out["childhood"] == [5]


def test_rank_tie_resolves_to_exactly_one_slug() -> None:
    raw = {"childhood": [4], "festivals": [4]}
    out = dedupe_assignments(raw)
    assert (4 in out["childhood"]) ^ (4 in out["festivals"])


def test_order_within_a_collection_is_preserved() -> None:
    raw = {"childhood": [3, 1, 2]}
    assert dedupe_assignments(raw)["childhood"] == [3, 1, 2]


async def test_curate_moments_scopes_tool_to_grid_slugs_and_dedupes() -> None:
    canned = {"collections": {s: [] for s in CURATED_SLUGS}}
    canned["collections"]["childhood"] = [0, 1]
    canned["collections"]["festivals"] = [1, 2]
    with patch(
        "flashback.storybook.curation.call_with_tool",
        new=AsyncMock(return_value=canned),
    ) as llm:
        out = await curate_moments(
            settings=_SETTINGS,
            subject_name="Subject",
            relationship="Grand Father",
            moments=[
                {"title": f"t{i}", "narrative": f"n{i}"} for i in range(3)
            ],
        )
    tool = llm.call_args.kwargs["tool"]
    assert set(tool.input_schema["properties"]["collections"]["properties"]) == set(
        CURATED_SLUGS
    )
    # 1 sits at rank 0 in festivals but rank 1 in childhood -> festivals keeps it.
    assert out["childhood"] == [0]
    assert out["festivals"] == [1, 2]


async def test_out_of_range_indices_are_dropped() -> None:
    canned = {"collections": {s: [] for s in CURATED_SLUGS}}
    canned["collections"]["childhood"] = [0, 99, -1]
    with patch(
        "flashback.storybook.curation.call_with_tool",
        new=AsyncMock(return_value=canned),
    ):
        out = await curate_moments(
            settings=_SETTINGS,
            subject_name="S",
            relationship=None,
            moments=[{"title": "t", "narrative": "n"}],
        )
    assert out["childhood"] == [0]
