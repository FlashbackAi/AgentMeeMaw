"""Render structured response-generation context into prompt text."""

from __future__ import annotations

from flashback.llm.prompt_safety import xml_text
from flashback.onboarding.archetypes import render_archetype_answers_natural_language
from flashback.response_generator.schema import (
    FirstTimeOpenerContext,
    StarterContext,
    TurnContext,
)


def render_turn_context(ctx: TurnContext) -> str:
    sections: list[str] = [_render_subject(ctx.person_name, ctx.person_relationship, ctx.person_gender)]

    if ctx.prior_session_summary.strip():
        sections.append(
            _block("prior_session_summary", xml_text(ctx.prior_session_summary.strip()))
        )

    if ctx.rolling_summary.strip():
        sections.append(_block("rolling_summary", ctx.rolling_summary.strip()))

    if ctx.recent_turns:
        lines = [f"{turn.role}: {xml_text(turn.content)}" for turn in ctx.recent_turns]
        sections.append(_block("recent_turns", "\n".join(lines)))

    sections.append(
        f"<emotional_temperature>{ctx.emotional_temperature}</emotional_temperature>"
    )

    retrieval_sections: list[str] = []
    if ctx.related_moments:
        lines = []
        for moment in ctx.related_moments:
            similarity = ""
            if moment.similarity_score is not None:
                similarity = f"  (similarity: {moment.similarity_score:.2f})"
            lines.append(
                f"- {xml_text(moment.title)}: {xml_text(moment.narrative)}{similarity}"
            )
        retrieval_sections.append(_block("moments", "\n".join(lines)))

    if ctx.related_entities:
        lines = []
        for entity in ctx.related_entities:
            description = entity.description or ""
            lines.append(
                f"- {entity.kind} {xml_text(entity.name)}: {xml_text(description)}".rstrip()
            )
        retrieval_sections.append(_block("entities", "\n".join(lines)))

    if ctx.related_threads:
        lines = [
            f"- {xml_text(thread.name)}: {xml_text(thread.description)}"
            for thread in ctx.related_threads
        ]
        retrieval_sections.append(_block("threads", "\n".join(lines)))

    if retrieval_sections:
        sections.append(_block("retrieved_context", "\n".join(retrieval_sections)))

    if ctx.mentioned_entities:
        lines = []
        for entity in ctx.mentioned_entities:
            description = entity.description or ""
            lines.append(
                f"- {entity.kind} {xml_text(entity.name)}: {xml_text(description)}".rstrip()
            )
        attrs = ' ambiguous="true"' if ctx.ambiguous_mention else ""
        sections.append(
            "\n".join(
                [
                    f"<mentioned_entities{attrs}>",
                    "\n".join(lines),
                    "</mentioned_entities>",
                ]
            )
        )

    if ctx.seeded_question_text:
        sections.append(_block("seeded_question", xml_text(ctx.seeded_question_text)))

    if ctx.tap_pending:
        dim_attr = f' dimension="{ctx.tap_dimension}"' if ctx.tap_dimension else ""
        body = xml_text(ctx.tap_question_text or "")
        sections.append(f"<tap_pending{dim_attr}>{body}</tap_pending>")

    if ctx.current_theme_display_name:
        sections.append(
            _block(
                "current_theme",
                xml_text(ctx.current_theme_display_name),
            )
        )

    return "\n\n".join(sections)


def render_starter_context(ctx: StarterContext) -> str:
    sections = [_render_subject(ctx.person_name, ctx.person_relationship, ctx.person_gender)]
    if ctx.contributor_display_name:
        sections.append(_block("contributor_name", xml_text(ctx.contributor_display_name)))
    if ctx.current_theme_display_name:
        theme_block_lines: list[str] = [
            f'<current_theme kind="{xml_text(ctx.current_theme_kind or "")}">',
            xml_text(ctx.current_theme_display_name),
        ]
        if ctx.theme_archetype_answers:
            theme_block_lines.append("<archetype_answers>")
            for ans in ctx.theme_archetype_answers:
                text = _format_theme_archetype_answer(ans)
                if text:
                    theme_block_lines.append(f"- {xml_text(text)}")
            theme_block_lines.append("</archetype_answers>")
        theme_block_lines.append("</current_theme>")
        sections.append("\n".join(theme_block_lines))
    if ctx.anchor_dimension and ctx.anchor_question_text:
        sections.append(
            "\n".join(
                [
                    f'<anchor_question dimension="{ctx.anchor_dimension}">',
                    xml_text(ctx.anchor_question_text),
                    "</anchor_question>",
                ]
            )
        )
    elif ctx.anchor_question_text:
        sections.append(_block("seeded_question", xml_text(ctx.anchor_question_text)))
    if ctx.prior_session_summary and ctx.prior_session_summary.strip():
        sections.append(
            _block("prior_session_summary", xml_text(ctx.prior_session_summary.strip()))
        )
    return "\n\n".join(sections)


def _format_theme_archetype_answer(answer: dict) -> str:
    """Render a single archetype answer row as a short readable line.

    Expected shape: ``{'question_id', 'question_text'?, 'option_id'?,
    'option_label'?, 'free_text'?}``. Node decides what to pack; we
    accept several layouts gracefully.
    """
    question = (
        answer.get("question_text") or answer.get("text") or answer.get("question") or ""
    )
    chosen = (
        answer.get("option_label")
        or answer.get("label")
        or answer.get("free_text")
        or answer.get("answer")
        or ""
    )
    if question and chosen:
        return f"{question.strip()} — {chosen.strip()}"
    return chosen.strip() or question.strip()


def render_first_time_opener_context(ctx: FirstTimeOpenerContext) -> str:
    sections = [_render_subject(ctx.person_name, ctx.person_relationship, ctx.person_gender)]
    if ctx.contributor_display_name:
        sections.append(_block("contributor_name", xml_text(ctx.contributor_display_name)))
    rendered = render_archetype_answers_natural_language(
        ctx.archetype_answers,
        ctx.person_relationship,
        ctx.person_gender,
    )
    sections.append(_block("archetype_answers", xml_text(rendered)))
    if ctx.anchor_dimension and ctx.anchor_question_text:
        sections.append(
            "\n".join(
                [
                    f'<anchor_question dimension="{ctx.anchor_dimension}">',
                    xml_text(ctx.anchor_question_text),
                    "</anchor_question>",
                ]
            )
        )
    elif ctx.anchor_question_text:
        sections.append(_block("seeded_question", xml_text(ctx.anchor_question_text)))
    return "\n\n".join(sections)


_PRONOUN_MAP = {
    "he": "he/him/his",
    "she": "she/her/hers",
    "they": "they/them/theirs",
}


def _render_subject(name: str, relationship: str | None, gender: str = "they") -> str:
    lines = ["<subject>", f"Name: {xml_text(name)}"]
    if relationship:
        lines.append(f"Relationship to contributor: {xml_text(relationship)}")
    pronouns = _PRONOUN_MAP.get(gender, "they/them/theirs")
    lines.append(f"Pronouns: {pronouns}")
    lines.append("</subject>")
    return "\n".join(lines)


def _block(name: str, content: str) -> str:
    return "\n".join([f"<{name}>", content, f"</{name}>"])
