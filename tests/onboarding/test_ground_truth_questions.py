from flashback.onboarding.archetypes import (
    GROUND_TRUTH_QUESTIONS,
    expected_question_ids,
    ground_truth_writes_from_answers,
    public_questions_for_relationship,
    questions_for_archetype,
)


def test_every_archetype_gets_the_two_gt_questions():
    for archetype in ("friend", "parent", "generic"):
        ids = [q["id"] for q in questions_for_archetype(archetype)]
        assert "gt_region" in ids
        assert "gt_birth_era" in ids


def test_expected_ids_include_gt_questions():
    ids = expected_question_ids("friend")
    assert {"gt_region", "gt_birth_era"} <= ids


def test_public_questions_strip_ground_truth_field_key():
    _, questions = public_questions_for_relationship("friend")
    gt_q = next(q for q in questions if q["id"] == "gt_region")
    assert "ground_truth_field" not in gt_q
    assert all("implies" not in o for o in gt_q["options"])


def test_gt_questions_have_four_options_and_skip():
    for question in GROUND_TRUTH_QUESTIONS:
        assert len(question["options"]) == 4
        assert question["allow_skip"] is True
        assert question["allow_free_text"] is True


def test_ground_truth_writes_from_answers():
    answers = [
        {"question_id": "gt_region", "option_id": None,
         "free_text": "Karimnagar, Telangana", "label": None},
        {"question_id": "gt_birth_era", "option_id": "era_50s_60s",
         "label": "1950s or 60s"},
        {"question_id": "friend_meet", "option_id": "school",
         "label": "Through school"},
        {"question_id": "gt_region", "skipped": True},
    ]
    writes = ground_truth_writes_from_answers(answers)
    assert ("region", "Karimnagar, Telangana") in writes
    assert ("birth_era", "1950s or 60s") in writes
    assert len(writes) == 2  # non-GT and skipped answers ignored
