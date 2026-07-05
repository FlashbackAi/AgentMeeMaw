from __future__ import annotations

import pytest

from flashback.onboarding.archetypes import (
    archetype_for_relationship,
    merge_implies,
    public_questions_for_relationship,
    render_archetype_answers_natural_language,
    resolve_options,
    sanitize_implies,
)


def test_relationship_aliases_cover_friend_and_never_met_ancestor() -> None:
    assert archetype_for_relationship("best friend") == "friend"
    assert archetype_for_relationship("ancestor I never met") == "ancestor_never_met"
    assert archetype_for_relationship("family friend from temple") == "friend"
    assert archetype_for_relationship("") == "generic"


def test_public_questions_strip_server_side_implies() -> None:
    archetype, questions = public_questions_for_relationship("best friend")

    assert archetype == "friend"
    # 5 relationship questions + 3 appended universal questions + the 2
    # appended ground-truth questions = 10 (onboarding lands at 10).
    assert len(questions) == 10
    assert questions[0]["id"] == "friend_meet"
    assert questions[0]["allow_free_text"] is True
    assert questions[0]["allow_skip"] is True
    assert "implies" not in questions[0]["options"][0]


def test_public_questions_render_subject_pronouns() -> None:
    _, questions = public_questions_for_relationship("father", gender="he")

    assert questions[0]["text"] == "When you picture him at home, what comes back first?"
    assert questions[0]["options"][0]["label"] == "His voice"
    assert questions[0]["options"][1]["label"] == "His face"
    assert questions[1]["text"] == "What was he like on an ordinary day?"


def test_every_archetype_has_three_to_five_questions() -> None:
    """The onboarding contract is 3-5 questions per relationship.
    Fewer feels survey-light; more is form fatigue."""

    from flashback.onboarding.archetypes import ARCHETYPES

    for archetype, questions in ARCHETYPES.items():
        assert 3 <= len(questions) <= 5, (
            f"archetype {archetype!r} has {len(questions)} questions, "
            "expected 3-5"
        )


def test_sanitize_implies_keeps_only_known_shapes() -> None:
    implies = sanitize_implies(
        {
            "coverage": ["place", "nonsense"],
            "life_period_estimate": "school years",
            "entities": [
                {"type": "place", "name": "College", "description": "Met there"},
                {"type": "planet", "name": "Mars"},
                {"kind": "person", "name": "Auntie"},
            ],
        }
    )

    assert implies["coverage"] == ["place", "era"]
    assert implies["life_period_estimate"] == "school years"
    assert implies["entities"] == [
        {"type": "place", "name": "College", "description": "Met there"},
        {"type": "person", "name": "Auntie"},
    ]


def test_archetype_answers_render_as_opener_context() -> None:
    rendered = render_archetype_answers_natural_language(
        [
            {
                "question_id": "friend_meet",
                "option_id": "school",
                "label": "Through school",
            },
            {
                "question_id": "friend_usual_activity",
                "option_id": None,
                "free_text": "We would talk for hours",
            },
            {"question_id": "friend_shared_place", "skipped": True},
        ],
        "friend",
    )

    assert "How did you two first meet? Through school." in rendered
    assert "What did you usually do together? We would talk for hours." in rendered
    assert "friend_shared_place" not in rendered


def test_public_questions_carry_allow_multiple_flag() -> None:
    _, questions = public_questions_for_relationship("best friend")
    by_id = {q["id"]: q for q in questions}

    # Relationship + universal questions are multi-select.
    assert by_id["friend_meet"]["allow_multiple"] is True
    assert by_id["universal_their_work"]["allow_multiple"] is True
    # The ground-truth pair stays single-choice (writes ONE value into
    # persons.ground_truth).
    assert by_id["gt_region"]["allow_multiple"] is False
    assert by_id["gt_birth_era"]["allow_multiple"] is False
    # ground_truth_field stays server-only.
    assert "ground_truth_field" not in by_id["gt_region"]


def test_resolve_options_multi_select_and_dedup() -> None:
    question, options = resolve_options(
        relationship="friend",
        question_id="friend_meet",
        option_ids=["school", "work", "school"],
    )
    assert question["id"] == "friend_meet"
    assert [o["id"] for o in options] == ["school", "work"]


def test_resolve_options_rejects_multi_on_ground_truth() -> None:
    with pytest.raises(ValueError, match="single option"):
        resolve_options(
            relationship="friend",
            question_id="gt_region",
            option_ids=["same_place", "abroad"],
        )


def test_resolve_options_rejects_unknown_option() -> None:
    with pytest.raises(ValueError, match="unknown option_id"):
        resolve_options(
            relationship="friend",
            question_id="friend_meet",
            option_ids=["school", "nonsense"],
        )


def test_merge_implies_unions_coverage_and_dedupes_entities() -> None:
    merged = merge_implies(
        [
            {
                "coverage": ["place", "relation"],
                "entities": [{"type": "place", "name": "school"}],
            },
            {
                "coverage": ["relation", "voice"],
                "entities": [
                    {"type": "place", "name": "School"},
                    {"type": "person", "name": "mutual friends"},
                ],
            },
        ]
    )
    assert merged["coverage"] == ["place", "relation", "voice"]
    assert [e["name"] for e in merged["entities"]] == ["school", "mutual friends"]


def test_merge_implies_drops_conflicting_life_periods() -> None:
    merged = merge_implies(
        [
            {"coverage": ["place"], "life_period_estimate": "school years"},
            {"coverage": ["era"], "life_period_estimate": "working years"},
        ]
    )
    assert "life_period_estimate" not in merged

    agreed = merge_implies(
        [
            {"coverage": ["place"], "life_period_estimate": "school years"},
            {"coverage": ["relation"], "life_period_estimate": "school years"},
        ]
    )
    assert agreed["life_period_estimate"] == "school years"
    assert "era" in agreed["coverage"]


def test_archetype_answers_render_multi_labels_with_free_text() -> None:
    rendered = render_archetype_answers_natural_language(
        [
            {
                "question_id": "friend_usual_activity",
                "option_ids": ["talk", "eat"],
                "labels": ["Talk for hours", "Eat together"],
                "free_text": "played carrom on Sundays",
            },
        ],
        "friend",
    )

    assert (
        "What did you usually do together? Talk for hours, Eat together — "
        'and in their own words: "played carrom on Sundays".' in rendered
    )


def test_archetype_answers_render_pronouned_question_and_label() -> None:
    rendered = render_archetype_answers_natural_language(
        [
            {
                "question_id": "parent_home_picture",
                "option_id": "voice",
                "label": "Their voice",
            },
        ],
        "father",
        gender="he",
    )

    assert "When you picture him at home, what comes back first? His voice." in rendered
