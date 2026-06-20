"""The tribute archetype count is wider than the universal default."""

from __future__ import annotations

import inspect

from flashback.themes.archetype_llm import generate_archetype_questions
from flashback.tribute.theme import TRIBUTE_ARCHETYPE_MAX, TRIBUTE_ARCHETYPE_MIN


def test_universal_defaults_are_3_to_4() -> None:
    sig = inspect.signature(generate_archetype_questions)
    assert sig.parameters["min_questions"].default == 3
    assert sig.parameters["max_questions"].default == 4


def test_tribute_count_is_wider() -> None:
    assert TRIBUTE_ARCHETYPE_MIN >= 5
    assert TRIBUTE_ARCHETYPE_MAX >= TRIBUTE_ARCHETYPE_MIN
    assert TRIBUTE_ARCHETYPE_MAX <= 22


def test_fathers_day_bank_is_22() -> None:
    from flashback.tribute.theme import build_fathers_day_archetype_questions

    questions = build_fathers_day_archetype_questions()
    assert len(questions) == 22
    # Every question keeps >= 2 chip options (build drops degenerate ones).
    assert all(len(q.options) >= 2 for q in questions)
