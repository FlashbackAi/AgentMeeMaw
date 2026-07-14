"""Generate-first drafting call: schema routing + cost tagging."""

from __future__ import annotations

from dataclasses import dataclass

from flashback.tribute import config_llm


@dataclass
class _Settings:
    llm_big_provider: str = "anthropic"
    llm_big_model: str = "claude-sonnet-4-6"


async def test_profile_kind_uses_profile_tool_and_feature(monkeypatch) -> None:
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return {"display_name": "Friend"}

    monkeypatch.setattr(config_llm, "call_with_tool", fake)
    out = await config_llm.generate_config_draft(
        _Settings(), kind="profile", relationship_group="friend",
        occasion="Friendship Day", brief="fun and teasing",
    )
    assert out == {"display_name": "Friend"}
    assert captured["tool"].name == "draft_relationship_profile"
    assert captured["feature"] == "tribute_config_generate"
    assert "<brief>fun and teasing</brief>" in captured["user_message"]
    assert "<occasion>Friendship Day</occasion>" in captured["user_message"]
    # third-person + {name} rules ride the system prompt
    assert "THIRD-PERSON" in captured["system_prompt"]
    assert "{name}" in captured["system_prompt"]


async def test_campaign_kind_uses_campaign_tool(monkeypatch) -> None:
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return {"display_name": "Raksha Bandhan"}

    monkeypatch.setattr(config_llm, "call_with_tool", fake)
    await config_llm.generate_config_draft(
        _Settings(), kind="campaign", occasion="Raksha Bandhan",
        brief="sibling bond",
    )
    assert captured["tool"].name == "draft_campaign"


async def test_non_dict_output_degrades_to_empty(monkeypatch) -> None:
    async def fake(**kw):
        return "not a dict"

    monkeypatch.setattr(config_llm, "call_with_tool", fake)
    out = await config_llm.generate_config_draft(
        _Settings(), kind="profile", brief="b"
    )
    assert out == {}
