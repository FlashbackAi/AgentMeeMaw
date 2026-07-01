import pytest
from flashback.orchestrator import tap_options

pytestmark = pytest.mark.asyncio


async def test_onboarding_prompt_is_indirect_and_names_subject(monkeypatch):
    async def _fake_options(**kwargs):
        return ["Her quick smile", "Sunday mornings", "Always cooking", "On the porch"]
    monkeypatch.setattr(tap_options, "generate_tap_options", _fake_options)
    text, options = await tap_options.generate_onboarding_tap(
        settings=object(), person_name="David", relationship="his daughter",
    )
    assert "David" in text
    lowered = text.lower()
    assert "what did" not in lowered and "mean to you" not in lowered
    assert "relationship" not in lowered
    assert options == ["Her quick smile", "Sunday mornings", "Always cooking", "On the porch"]


async def test_tap_options_pass_neutral_pronouns_by_default_and_subject_pronouns_when_known(
    monkeypatch,
):
    from types import SimpleNamespace

    captured = {}

    async def _fake_call(**kwargs):
        captured.update(kwargs)
        return {"options": ["a", "b", "c", "d"]}

    monkeypatch.setattr(tap_options, "call_with_tool", _fake_call)
    settings = SimpleNamespace(llm_small_provider="openai", llm_intent_model="gpt-5.1")

    # Unknown gender -> neutral pronouns (we must not assume he/she for the subject).
    await tap_options.generate_tap_options(
        settings=settings, question_text="When you picture David...",
        person_name="David", person_relationship=None, dimension="", person_gender=None,
    )
    assert 'pronouns="they/them/theirs"' in captured["user_message"]

    # Known gender -> that subject's pronouns.
    await tap_options.generate_tap_options(
        settings=settings, question_text="When you picture Margaret...",
        person_name="Margaret", person_relationship=None, dimension="", person_gender="she",
    )
    assert 'pronouns="she/her/hers"' in captured["user_message"]

    # The prompt must forbid guessing a gender.
    assert "NEVER guess a gender" in tap_options._TAP_OPTIONS_SYSTEM


async def test_onboarding_options_fall_back_to_empty(monkeypatch):
    async def _fail(**kwargs):
        return []
    monkeypatch.setattr(tap_options, "generate_tap_options", _fail)
    text, options = await tap_options.generate_onboarding_tap(
        settings=object(), person_name="David", relationship=None,
    )
    assert text and options == []
