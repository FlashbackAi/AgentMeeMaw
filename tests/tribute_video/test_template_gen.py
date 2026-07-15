"""Template-generation prompt: the brief owns the style, geometry stays hard."""

from __future__ import annotations

from flashback.tribute_video.template_gen import (
    _DEFAULT_BRIEF,
    build_template_prompt,
)


def test_brief_leads_the_prompt() -> None:
    """2026-07-16: with the contract first, every candidate anchored on the
    contract's own vocabulary and came out in the Father's Day register.
    The brief must appear before the layout contract."""
    brief = "playful comic-strip border, sunny yellow and sky blue"
    p = build_template_prompt(brief)
    assert p.index(brief) < p.index("PAGE BACKGROUND TEMPLATE")


def test_contract_has_no_style_vocabulary() -> None:
    p = build_template_prompt("neon arcade border")
    contract = p[p.index("PAGE BACKGROUND TEMPLATE"):]
    for style_word in ("keepsake", "hand-painted", "storybook"):
        assert style_word not in contract.lower()


def test_geometry_constraints_survive() -> None:
    p = build_template_prompt("anything")
    assert "outer 8 percent" in p
    assert "STRICTLY FORBIDDEN" in p
    assert "single continuous sheet" in p


def test_empty_brief_gets_default_style() -> None:
    p = build_template_prompt("   ")
    assert _DEFAULT_BRIEF in p
