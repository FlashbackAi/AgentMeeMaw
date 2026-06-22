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


async def test_onboarding_options_fall_back_to_empty(monkeypatch):
    async def _fail(**kwargs):
        return []
    monkeypatch.setattr(tap_options, "generate_tap_options", _fail)
    text, options = await tap_options.generate_onboarding_tap(
        settings=object(), person_name="David", relationship=None,
    )
    assert text and options == []
