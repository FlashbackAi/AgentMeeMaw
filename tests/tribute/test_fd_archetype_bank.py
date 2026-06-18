"""The fixed Father's Day archetype question bank."""

from __future__ import annotations

from flashback.tribute.theme import (
    FATHERS_DAY_ARCHETYPE_BANK,
    build_fathers_day_archetype_questions,
)


def test_bank_is_nonempty_and_each_has_options() -> None:
    assert len(FATHERS_DAY_ARCHETYPE_BANK) >= 6
    for text, options in FATHERS_DAY_ARCHETYPE_BANK:
        assert text.strip()
        assert len(options) >= 2


def test_builder_produces_stable_ids_and_valid_options() -> None:
    qs = build_fathers_day_archetype_questions()
    assert len(qs) == len(FATHERS_DAY_ARCHETYPE_BANK)
    assert qs[0].question_id == "q1"
    first_opt = qs[0].options[0]
    assert first_opt["option_id"] == "q1_o1"
    assert first_opt["label"]
    assert all(len(q.options) >= 2 for q in qs)
