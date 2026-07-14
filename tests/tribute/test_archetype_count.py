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


def test_generation_accepts_occasion_extra_context() -> None:
    # The tribute CRM passes a campaign's occasion framing into generation;
    # the FD bank itself now lives in the 0039 seed (tests/db).
    sig = inspect.signature(generate_archetype_questions)
    assert sig.parameters["extra_context"].default == ""
