from __future__ import annotations

from flashback.response_generator.context import (
    render_starter_context,
    render_turn_context,
)
from tests.response_generator.fixtures.sample_contexts import (
    sample_starter_context,
    sample_turn_context,
)


def test_render_turn_context_produces_expected_sections():
    rendered = render_turn_context(sample_turn_context("recall"))

    assert "<subject>" in rendered
    assert "Name: Maya" in rendered
    assert "<rolling_summary>" in rendered
    assert "<recent_turns>" in rendered
    assert "user: The porch light was always on." in rendered
    assert "<emotional_temperature>medium</emotional_temperature>" in rendered
    assert "<retrieved_context>" in rendered
    assert "- Porch evenings: Maya sat on the porch after dinner.  (similarity: 0.32)" in rendered
    assert "- place Porch: The front porch at the family house." in rendered
    assert "- Evening routines: Small rituals that made home feel steady." in rendered


def test_empty_retrieval_sections_are_omitted_entirely():
    ctx = sample_turn_context("story")
    ctx.related_moments = []
    ctx.related_entities = []
    ctx.related_threads = []

    rendered = render_turn_context(ctx)

    assert "<retrieved_context>" not in rendered
    assert "<moments>" not in rendered
    assert "<entities>" not in rendered
    assert "<threads>" not in rendered


def test_render_starter_context_includes_anchor_text_and_dimension():
    rendered = render_starter_context(sample_starter_context())

    assert '<anchor_question dimension="sensory">' in rendered
    assert "What's a smell that brings them right back?" in rendered
    assert "Name: Maya" in rendered


def test_missing_prior_session_summary_omits_section():
    rendered = render_starter_context(sample_starter_context())

    assert "<prior_session_summary>" not in rendered
