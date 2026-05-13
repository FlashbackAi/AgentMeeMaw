from __future__ import annotations

from flashback.onboarding.archetypes import (
    archetype_for_relationship,
    public_questions_for_relationship,
    render_archetype_answers_natural_language,
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
    assert 3 <= len(questions) <= 5
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
